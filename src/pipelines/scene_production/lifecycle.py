"""Scene submission and retry decisions; persistence owns transactions and fences."""

import json
from pathlib import Path
from storage.files import safe_path, write_json
from capabilities.scene_authoring.program_submission import submission_identity
from capabilities.scene_authoring.validation.acceptance import (
    SCENE_ACCEPTANCE_POLICY_VERSION, admit_program, native_capture, require_accepted, verify_capture_versions,
)
from capabilities.scene_authoring.validation.policy import policy_for, check_references
from capabilities.isaac_sim.scene_requirements import validate_scene_requirements
from pipelines.scene_production.service_failures import (
    is_execution_diagnosis, is_service_failure, verify_capture_service_recovery,
)


def review_retry_evidence(directory, unit, *, archived):
    """Bind a re-review to the same admitted native capture and every planned view."""
    relative = (f'previous_scene_review/before_policy_{SCENE_ACCEPTANCE_POLICY_VERSION}/scene_acceptance.json'
                if archived else 'scene_acceptance.json')
    receipt_path = safe_path(directory, relative)
    if not receipt_path.is_file():
        raise ValueError('Review-only retry requires a saved failed scene acceptance')
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    versions = receipt.get('evidence_versions')
    verify_capture_versions(directory, versions)
    report = json.loads((directory / 'capture/report.json').read_text(encoding='utf-8'))
    evidence = json.loads((directory / 'capture/evidence.json').read_text(encoding='utf-8'))
    program = json.loads((directory / 'program.json').read_text(encoding='utf-8'))
    token = unit.get('capture_token')
    camera_count = len(program.get('cameras', []))
    frame_ids = {row.get('frame_id') for row in evidence.get('frames', []) if isinstance(row, dict)}
    if (not token or any(row.get(key) != token for row, key in (
            (receipt, 'capture_token'), (report, 'token'), (evidence, 'token')))
            or report.get('status') != 'captured' or report.get('renderable') is not True
            or not native_capture(report, evidence, directory / 'capture') or not camera_count
            or any(f'capture/view_{index}.png' not in versions or f'view_{index}' not in frame_ids
                   for index in range(camera_count))):
        raise ValueError('Review-only retry needs the original native capture token and all frozen camera views')
    return receipt


class SceneLifecycle:
    # 绑定场景工作区，供策略解析和生命周期操作使用。
    def __init__(self, root):
        self.root = Path(root)

    # 提取提交程序及其回执的稳定身份，用于幂等任务比对。
    def submission_identity(self, submission):
        return submission_identity(submission)

    # 按工作流及执行模式校验引用与显式程序要求，决定是否准入场景任务。
    def admit(self, intents, workflow, submitted_programs):
        frozen_design = None
        scene_modes = (
            [intent.get("execution_mode") for intent in intents] if workflow == "scene" else []
        )
        policies = {mode: policy_for(self.root, mode) for mode in set(scene_modes)}
        policy = next((value for value in policies.values() if value), {})
        if policy:
            check_references(intents, policy)
            requires_scene_programs = workflow == "scene" and any(
                mode in (None, "production") for mode in scene_modes
            )
            if requires_scene_programs:
                if (
                    not submitted_programs
                    or len(submitted_programs) != len(intents)
                    or any(not row for row in submitted_programs)
                ):
                    raise ValueError(
                        "Realistic production requires complete SceneProgram snapshots; no planner fallback"
                    )
                for intent, submission in zip(intents, submitted_programs):
                    mode = intent.get("execution_mode")
                    admission = admit_program(
                        self.root, submission["program"], allow_generated=True, execution_mode=mode
                    )
                    if len(intents) == 1:
                        declaration = admission.get('scene_brief') or admission.get('geometry_exception') or {}
                        frozen_design = {k: declaration[k] for k in ('id','scene_design_id','plan_path','plan_version') if k in declaration}
                    lineage_task_id = intent.get("refine_task_id") or intent.get("extend_task_id")
                    if lineage_task_id:
                        from capabilities.scene_authoring.contracts.scene_design import (
                            require_prior_brief,
                        )

                        require_prior_brief(
                            admission.get("scene_brief") or {},
                            self.root
                            / lineage_task_id
                            / (
                                "revision_"
                                + str(intent.get("refine_revision", intent.get("extend_revision")))
                            ),
                        )
        for submission in submitted_programs or []:
            if submission:
                validate_scene_requirements(submission["program"])
        return frozen_design

    # 在场景工作流完成前要求有效的生产验收回执。
    def require_success(self, directory, unit):
        if unit.get("workflow", "scene") == "scene":
            require_accepted(self.root, directory, unit.get("intent", {}).get("execution_mode"))

    def review_only_requested(self, unit):
        return unit.get('scene_review_policy_version') == SCENE_ACCEPTANCE_POLICY_VERSION

    def review_only(self, task):
        unit = task['unit']
        mode = unit['intent'].get('execution_mode')
        if unit.get('workflow', 'scene') != 'scene' or unit['phase'] != 'review' or not policy_for(self.root, mode):
            raise ValueError('Review-only retry requires a production scene in the review phase')
        from capabilities.scene_authoring.contracts.scene_design import capture_retry_directory

        directory = capture_retry_directory(self.root, task['id'], unit['revision'])
        receipt = review_retry_evidence(directory, unit, archived=False)
        old_policy = receipt.get('policy_version', 1)
        if (receipt.get('enforced') is not True or receipt.get('passed') is not False
                or type(old_policy) is not int or old_policy >= SCENE_ACCEPTANCE_POLICY_VERSION):
            raise ValueError('Review-only retry requires a failed acceptance under an older scene policy')
        admit_program(self.root, json.loads((directory / 'program.json').read_text(encoding='utf-8')), execution_mode=mode)
        return {**unit, 'scene_review_policy_version': SCENE_ACCEPTANCE_POLICY_VERSION}

    # 依据失败阶段、操作反馈和恢复模式构造下一次尝试，同时保留既有成功产物。
    def validate_retry(self, task, feedback, directory_for):
        """Validate frozen scene and service inputs without holding a database transaction."""
        tid = task["id"]
        recovery_id = None
        failure = task.get('failure') or {}
        remote = failure.get('remote_job_failure') or {}
        export_probe = (remote.get('operation') in ('scene.export', 'extension.scene.export')
                        and (directory_for(tid, task['unit']['revision']) /
                             'isaac_sim/service_recovery.json').is_file())
        if is_execution_diagnosis(failure) and not export_probe:
            raise ValueError(
                'Inspect the retained Isaac Sim failure and correct the asset or execution path '
                'before retrying; unchanged capture is blocked'
            )
        service_recovery = export_probe or is_service_failure(failure)
        settings_changed = False
        if service_recovery:
            if task['unit']['phase'] != 'capture' or feedback is not None:
                raise ValueError('Resolve the service contract and recheck the saved capture; scene repair feedback cannot fix this failure')
            settings_changed = verify_capture_service_recovery(
                task, directory_for(tid, task['unit']['revision']))
        if policy_for(self.root) and task["unit"].get("workflow", "scene") == "scene":
            from capabilities.scene_authoring.contracts.scene_design import capture_retry_directory
            from capabilities.scene_authoring.contracts.scene_design import review_retry_directory

            mode = task["unit"].get("intent", {}).get("execution_mode")
            capture_failure = (
                task["unit"]["phase"] == "capture"
                and (task.get("failure") or {}).get("stage")
                in {"capture_transport", "capture_execution", "capture_service_contract"}
                and feedback is None
            )
            review_failure = (
                task["unit"]["phase"] == "review"
                and feedback is None
                and (task.get("failure") or {}).get("error")
                in {
                    "TimeoutError",
                    "URLError",
                    "ConnectionError",
                    "OSError",
                    "RemoteDisconnected",
                    "ConnectionResetError",
                    "ConnectionAbortedError",
                }
            )
            if not capture_failure and not review_failure:
                raise ValueError(
                    "Only review transport or capture execution/transport failures can retry; repair scene quality through capture/refine"
                )
            directory = (
                capture_retry_directory(self.root, tid, task["unit"]["revision"])
                if capture_failure
                else review_retry_directory(self.root, tid)
            )
            admit_program(
                self.root, json.loads((directory / "program.json").read_text()), execution_mode=mode
            )
            if review_failure and task["unit"].get("review_transport_retries", 0) >= 2:
                marker = self.root / "model_transport_recovery.json"
                recovery = (
                    json.loads(marker.read_text())
                    if marker.is_file() and marker.stat().st_size < 4096
                    else {}
                )
                recovery_id = (
                    recovery.get("recovery_id")
                    if recovery.get("status") == "transport_verified"
                    else None
                )
                if (
                    not isinstance(recovery_id, str)
                    or not recovery_id
                    or recovery_id == task["unit"].get("review_transport_recovery_id")
                ):
                    raise ValueError("Review transport recovery budget exhausted")
        return settings_changed, recovery_id

    def retry(self, task, feedback, capture_mode, directory_for, *, validation=None):
        if capture_mode == 'review_only':
            if feedback is not None:
                raise ValueError('Review-only retry uses frozen evidence and accepts no repair feedback')
            return self.review_only(task)
        settings_changed, recovery_id = validation if validation is not None else self.validate_retry(
            task, feedback, directory_for)
        tid = task['id']
        unit = dict(task["unit"])
        phase = unit["phase"]
        old = directory_for(tid, unit["revision"])
        if capture_mode != "auto" and not (
            phase == "capture"
            and (task.get("failure") or {}).get("stage")
            in {"capture_transport", "capture_execution", "capture_service_contract"}
        ):
            raise ValueError("capture_mode applies only to capture execution/transport failures")
        if recovery_id:
            unit.update(
                review_transport_recovery_id=recovery_id,
                previous_review_transport_retries=unit.get("review_transport_retries", 0),
                review_transport_retries=0,
            )
        if policy_for(self.root) and phase == "review":
            unit["review_transport_retries"] = unit.get("review_transport_retries", 0) + 1
        if phase == "capture" and (task.get("failure") or {}).get("stage") in {
            "capture_transport",
            "capture_execution",
            "capture_service_contract",
        }:
            from capabilities.isaac_sim.recovery import prepare_retry

            unit = prepare_retry(
                directory_for(tid, unit["revision"]),
                unit,
                'restart' if settings_changed else capture_mode,
                retired_protocol=(task.get("failure") or {}).get("code")
                == "CAPTURE_PROTOCOL_MIGRATED",
            )
        if feedback is not None:
            if phase != "plan":
                raise ValueError(
                    "retry feedback requires a failed planning stage; use refine for captured scenes"
                )
            unit["operator_feedback"] = feedback
        if phase == "plan":
            # Failed structural proposals and model traces remain immutable.
            # A repaired planner must not replay cached invalid responses.
            if unit.get("quality_repairs"):
                unit.setdefault("quality_source_revision", unit["revision"] - 1)
            if (old / "repair_seed.json").exists():
                import shutil

                shutil.copy2(
                    old / "repair_seed.json",
                    directory_for(tid, unit["revision"] + 1) / "repair_seed.json",
                )
            else:
                seeds = sorted(
                    old.glob("model_calls/scene_plan_*/validation_error.json"),
                    key=lambda p: int(p.parent.name.rsplit("_", 1)[-1]),
                    reverse=True,
                )
                for seed_path in seeds:
                    seed = json.loads(seed_path.read_text())
                    if isinstance(seed.get("proposal"), dict):
                        write_json(
                            directory_for(tid, unit["revision"] + 1) / "repair_seed.json", seed
                        )
                        break
            unit.update(revision=unit["revision"] + 1, engineering_retry=True)
        if phase == "review" and (old / "capture" / "report.json").exists():
            report = json.loads((old / "capture" / "report.json").read_text())
            if report["status"] != "captured":
                unit.update(revision=unit["revision"] + 1, phase="capture", engineering_retry=True)
                import shutil

                shutil.copy2(
                    old / "program.json", directory_for(tid, unit["revision"]) / "program.json"
                )
                unit.pop("capture_token", None)
        return unit
