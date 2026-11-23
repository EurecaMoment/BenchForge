"""Persist service ownership and require fresh discovery before capture recovery."""
from copy import deepcopy
import json


SERVICE_OWNER = 'service_configuration_or_adapter'
SERVICE_STAGE = 'capture_service_contract'


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
    """只按结构化归属识别服务阻断，兼容旧传输失败且不猜测错误字符串。"""
    if isinstance(value, dict):
        if value.get('repair_owner') == SERVICE_OWNER or value.get('stage') == SERVICE_STAGE:
            return True
        return any(is_service_failure(row) for row in failure_issues(value))
    return any(is_service_failure(row) for row in failure_issues(value))


def remote_job_failure(value):
    """Return a bounded receipt for an Isaac terminal-job failure, if present."""
    job = getattr(value, 'job', None)
    if not isinstance(job, dict):
        return None
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
        failure.update(stage=SERVICE_STAGE, repair_owner=SERVICE_OWNER, retry_unchanged=False)
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
    return bool(frozen and frozen.get('settings') != settings)
