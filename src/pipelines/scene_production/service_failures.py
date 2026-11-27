"""Persist service ownership and require fresh discovery before capture recovery."""
from copy import deepcopy
import json


SERVICE_OWNER = 'service_configuration_or_adapter'
SERVICE_STAGE = 'capture_service_contract'
ANNOTATION_BINDING_DEFECT = 'mitsuba_annotation_shape_binding'


def capture_service_defect(value):
    """Identify the diagnosed annotation binding defect from a native receipt only."""
    job = (value.get('remote_job_failure') if isinstance(value, dict)
           else remote_job_failure(value))
    if not isinstance(job, dict):
        return None
    failure = job.get('failure') or {}
    if (job.get('operation') in {'scene.capture', 'extension.scene.capture'}
            and job.get('state') == 'FAILED'
            and failure.get('code') == 'FAILED_PRECONDITION'
            and failure.get('message') == "Module 'mitsuba' has no attribute 'ShapePtr'"):
        return ANNOTATION_BINDING_DEFECT
    return None


def failure_issues(value):
    """从异常或持久失败报告提取完整问题，保留所有字段及原始消息。"""
    if isinstance(value, dict):
        rows = value.get('issues', [])
        if isinstance(rows, list):
            return deepcopy([row for row in rows if isinstance(row, dict)])
        return failure_issues(rows)
    for name in ('issues', 'report', 'preflight'):
        nested = getattr(value, name, None)
        if isinstance(nested, list):
            return deepcopy([row for row in nested if isinstance(row, dict)])
        if isinstance(nested, dict):
            return failure_issues(nested)
    return []


def is_service_failure(value):
    """Recognize typed ownership and the explicitly diagnosed native binding defect."""
    if isinstance(value, dict):
        if value.get('repair_owner') == SERVICE_OWNER or value.get('stage') == SERVICE_STAGE:
            return True
        if capture_service_defect(value):
            return True
        return any(is_service_failure(row) for row in failure_issues(value))
    if capture_service_defect(value):
        return True
    return any(is_service_failure(row) for row in failure_issues(value))


def remote_job_failure(value):
    """Return a bounded receipt for an Isaac terminal-job failure, if present."""
    job = getattr(value, 'job', None)
    if not isinstance(job, dict):
        return None
    if isinstance(job.get('id'), str) and job['id'] and job.get('state') == 'failed':
        message = job.get('error')
        if isinstance(message, str):
            return {'job_id': job['id'], 'operation': 'simulation', 'state': 'FAILED',
                    'failure': {'message': message}, **{key: job[key] for key in
                    ('phase', 'frames_done', 'frames_total') if key in job}}
    failure = job.get('failure')
    if (not isinstance(job.get('job_id'), str) or not job['job_id']
            or not isinstance(job.get('operation'), str) or not job['operation']
            or job.get('state') != 'FAILED'
            or not isinstance(failure, dict)
            or not isinstance(failure.get('code'), str)
            or not isinstance(failure.get('message'), str)):
        return None
    return {key: deepcopy(job[key]) for key in
            ('job_id', 'request_id', 'operation', 'state', 'failure', 'artifacts', 'timings')
            if key in job}


def is_remote_job_failure(value):
    """Recognize a persisted native terminal receipt without guessing from prose."""
    return isinstance(value, dict) and isinstance(value.get('remote_job_failure'), dict)


def is_execution_diagnosis(value):
    """Return diagnosis evidence for typed native execution failures only."""
    if capture_service_defect(value):
        return False
    if isinstance(value, dict):
        if value.get('execution_diagnosis') == 'isaac_sim_execution':
            return True
        if is_remote_job_failure(value):
            return True
        return any(is_execution_diagnosis(row) for row in failure_issues(value))
    if remote_job_failure(value) is not None:
        return True
    return any(row.get('repair_owner') == 'isaac_sim_execution'
               for row in failure_issues(value))


def stage_failure(error, phase):
    """持久保存阶段异常及完整服务问题，避免把服务错配交给几何修复。"""
    failure = {'error': type(error).__name__, 'detail': str(error)[:1000]}
    issues = failure_issues(error)
    if issues:
        failure['issues'] = issues
    if phase == 'capture':
        failure.update(stage='capture_execution', native_receipts_retained=True)
        remote = remote_job_failure(error)
        if remote is not None:
            failure.update(remote_job_failure=remote, repair_owner='isaac_sim_execution',
                           retry_unchanged=False)
        issues = failure_issues(error)
        if any(row.get('repair_owner') == 'isaac_sim_execution' for row in issues):
            failure.update(execution_diagnosis='isaac_sim_execution',
                           repair_owner='isaac_sim_execution', retry_unchanged=False)
    if is_service_failure(error):
        failure.update(stage=SERVICE_STAGE, repair_owner=SERVICE_OWNER,
                       retry_unchanged=bool(issues) and all(row.get('retry_unchanged') is True for row in issues))
    return failure


def current_capture_settings(fallback=None):
    """优先读取当前统一模型配置，未提供配置路径时保留任务的冻结设置。"""
    from service_access.configuration import isaac_config
    from service_access.runtime_config import environment_value, read_json
    path = environment_value('BENCHFORGE_MODELS_CONFIG')
    return isaac_config(read_json(path)) if path else dict(fallback or {})


def require_live_service(program, settings, capture_options=None):
    """只读核对本次完整采集范围，服务仍不兼容时禁止提交或修改场景。"""
    from capabilities.capture.capture_scope import capture_scope
    from capabilities.isaac_sim.capture_contract import inspect_live_capture_service
    from capabilities.isaac_sim.scene_requirements import UnsupportedScene
    import grpc
    try:
        if not settings.get('grpc'):
            raise ValueError('Configure the Isaac Sim service before retrying this capture')
        issues = inspect_live_capture_service(program, settings,
            scope=capture_scope(program, capture_options))
    except (ValueError, OSError, grpc.RpcError) as error:
        if is_service_failure(error):
            raise
        issues = [{'code': 'service_contract_check_failed', 'path': '', 'message': str(error),
                   'severity': 'error', 'blocking': True, 'repair_owner': SERVICE_OWNER,
                   'retry_unchanged': False, 'basis': 'live_isaac_sim_schema'}]
    if issues:
        raise UnsupportedScene('\n'.join(row['message'] for row in issues), issues=issues)


def verify_capture_service_recovery(task, directory):
    """从保存程序重新发现服务，仅在实时核对通过后允许恢复原采集。"""
    unit = task['unit']
    frozen_path = directory / 'isaac_sim' / f"attempt_{unit.get('capture_generation', 0)}" / 'capture.json'
    frozen = json.loads(frozen_path.read_text(encoding='utf-8')) if frozen_path.is_file() else {}
    settings = current_capture_settings(frozen.get('settings'))
    program = json.loads((directory / 'program.json').read_text(encoding='utf-8'))
    require_live_service(program, settings, unit.get('intent', {}).get('capture_options'))
    failure = task.get('failure') or {}
    remote = failure.get('remote_job_failure') or {}
    if capture_service_defect(failure) or remote.get('operation') in ('scene.export', 'extension.scene.export'):
        verify_native_recovery(task, directory, settings)
    return bool(frozen and frozen.get('settings') != settings)


def verify_native_recovery(task, directory, settings):
    """Verify the repaired operation on the original endpoint before a fresh attempt."""
    from capabilities.isaac_sim.capture_contract import capture_service_client
    from capabilities.isaac_sim.grpc.transport import document
    from storage.files import write_json

    failed = task['failure']['remote_job_failure']
    marker = directory / 'isaac_sim/service_recovery.json'
    if not marker.is_file() or marker.stat().st_size > 4096:
        raise ValueError('Service recovery requires isaac_sim/service_recovery.json '
                         'binding the failed job to a verified simulator job')
    recovery = json.loads(marker.read_text(encoding='utf-8'))
    if (not isinstance(recovery, dict)
            or recovery.get('schema') != 'benchforge.isaac-service-recovery/v1'
            or recovery.get('failed_job_id') != failed['job_id']
            or not isinstance(recovery.get('verified_job_id'), str)
            or not recovery['verified_job_id']
            or recovery['verified_job_id'] == failed['job_id']):
        raise ValueError('Service recovery evidence must name this failed native job and a new verified job')
    generation = task['unit'].get('capture_generation', 0)
    exporting = failed['operation'] in ('scene.export', 'extension.scene.export')
    jobs = directory / 'isaac_sim' / f'attempt_{generation}/jobs'
    receipt = jobs / ('export' if exporting else 'views-0') / 'receipt.json'
    original = json.loads(receipt.read_text(encoding='utf-8')) if receipt.is_file() else {}
    failed_at = original.get('created_at_unix', 0)
    client = capture_service_client({'providers': {'IsaacSim4A100': settings}})
    try:
        if recovery.get('endpoint') != client.endpoint:
            raise ValueError('Service recovery evidence belongs to another configured Isaac service')
        job = document(client.get(recovery['verified_job_id']))
    finally:
        client.close()
    frames = (job.get('result') or {}).get('frames') or []
    artifacts = {row.get('artifact_id') for row in job.get('artifacts', []) if isinstance(row, dict)}

    def annotated(frame):
        if not isinstance(frame, dict):
            return False
        annotation = frame.get('annotations') or {}
        if not isinstance(annotation, dict):
            return False
        refs = (frame.get('image'), annotation.get('arrays'), annotation.get('labels'))
        return (all(isinstance(ref, str) and ref.startswith('artifact://')
                    and ref[len('artifact://'):] in artifacts for ref in refs)
                and annotation.get('depth_unit') == 'meter'
                and annotation.get('valid_pixels', 0) > 0
                and annotation.get('instance_count', 0) > 0
                and annotation.get('class_count', 0) > 0)

    if exporting:
        request = json.loads((receipt.parent / 'request.json').read_text(encoding='utf-8'))
        arguments = request['payload']['extension']['arguments']
        result = job.get('result') or {}
        valid_result = (job.get('operation') in ('scene.export', 'extension.scene.export')
                        and result.get('session_id') == arguments['session_id']
                        and all(isinstance(result.get(key), str) and result[key].startswith('artifact://')
                                and result[key].removeprefix('artifact://') in artifacts for key in ('scene', 'package')))
        detail = 'a USD scene and package from the same retained session'
    else:
        valid_result = (job.get('operation') in ('scene.capture', 'extension.scene.capture')
                        and frames and all(annotated(frame) for frame in frames))
        detail = 'RGB, metric depth and semantic/instance artifacts'
    if (original.get('job_id') != failed['job_id'] or not failed_at
            or job.get('job_id') != recovery['verified_job_id']
            or job.get('state') != 'SUCCEEDED'
            or job.get('created_at_unix', 0) <= failed_at
            or not valid_result):
        raise ValueError('Service recovery requires a newer SUCCEEDED native job with ' + detail)
    write_json(directory / 'isaac_sim/service_recovery/verified_receipt.json',
               {'schema': 'benchforge.isaac-service-recovery/v1', 'failed_job_id': failed['job_id'],
                'endpoint': recovery['endpoint'], 'verified_job': job})
