"""Scene submission and retry decisions; persistence owns transactions and fences."""

import json
from pathlib import Path
from storage.files import write_json
from capabilities.scene_authoring.program_submission import submission_identity
from capabilities.scene_authoring.validation.acceptance import admit_program, require_accepted
from capabilities.scene_authoring.validation.policy import policy_for, check_references
from capabilities.isaac_sim.scene_requirements import validate_scene_requirements
from pipelines.scene_production.service_failures import (
    is_service_failure, verify_capture_service_recovery,
)


class SceneLifecycle:
    # 绑定场景工作区，供策略解析和生命周期操作使用。
    def __init__(self, root):
        self.root = Path(root)

    # 提取提交程序及其回执的稳定身份，用于幂等任务比对。
    def submission_identity(self, submission):
        return submission_identity(submission)

    # 按工作流及执行模式校验引用与显式程序要求，决定是否准入场景任务。
    def admit(self, intents, workflow, submitted_programs):
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

    # 在场景工作流完成前要求有效的生产验收回执。
    def require_success(self, directory, unit):
        if unit.get("workflow", "scene") == "scene":
            require_accepted(self.root, directory, unit.get("intent", {}).get("execution_mode"))

    # 依据失败阶段、操作反馈和恢复模式构造下一次尝试，同时保留既有成功产物。
    def retry(self, task, feedback, capture_mode, directory_for):
        tid = task["id"]
        recovery_id = None
        service_recovery = is_service_failure(task.get('failure') or {})
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
