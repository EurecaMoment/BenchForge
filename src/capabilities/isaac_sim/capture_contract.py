"""Validate a SceneProgram against the v2 contract before any network call."""
from __future__ import annotations

import grpc

from capabilities.capture.capture_scope import capture_scope
from capabilities.isaac_sim.grpc.transport import PROTOCOL, SimulationClient, document
from capabilities.isaac_sim.scene_mapping import simulation_plan
from capabilities.isaac_sim.scene_requirements import UnsupportedScene, capture_requirement_issues
from service_access.configuration import ISAAC_PROVIDER


# 构造指向配置端点的仿真服务客户端。
def capture_service_client(config):
    return SimulationClient(config)


# 汇总静态契约限制，返回全部可一次性修复的问题。
def capture_service_issues(program, settings, scope=None):
    scope = scope or capture_scope(program, None)
    issues = list(capture_requirement_issues(program, None, settings))
    try:
        simulation_plan(program, settings, scope)
    except UnsupportedScene as error:
        issues.extend(error.issues)
    return issues


# 在提交任何任务前拒绝不支持的场景声明。
def require_capture_service(program, settings, scope=None):
    issues = capture_service_issues(program, settings, scope)
    if issues:
        raise UnsupportedScene('\n'.join(row['path'] + ': ' + row['message'] for row in issues),
                               issues=issues)


# 构造由部署或适配器负责的服务侧问题，阻止重试同一配置。
def _service_issue(code, message):
    return {'code': code, 'path': '', 'message': message, 'basis': PROTOCOL,
            'severity': 'error', 'blocking': True,
            'repair_owner': 'service_configuration_or_adapter', 'retry_unchanged': False}


# 把 Health 状态与契约版本转换为服务侧阻断问题。
def health_issues(health):
    import re as _re
    issues = []
    status = (health.get('status') or '').lower()
    if status != 'ready':
        detail = health.get('error') or f'Isaac Sim service is {status or "unknown"}'
        issues.append(_service_issue('service_unavailable', detail))
    # v2 契约能力校验：api_version 或 supported_shapes 至少有一项能匹配 BenchForge 预期。
    # 1) api_version 以 "isaacsim_rpc.v1" 开头 — 契约完全对齐，放行。
    # 2) api_version 是纯语义版本号（如 "1.0.0"）— 视为同协议不同版本，需同时 supported_shapes
    #    覆盖 cube/sphere/mesh 中的基本形状才放行。
    # 3) api_version 是其他命名空间（如 "a100sim.v1"）或完全缺失 — 不满足 1/2 即阻断。
    api_version = str(health.get('api_version') or '')
    supported = set(health.get('supported_shapes') or [])
    expected_shapes = {'cube', 'sphere', 'mesh'}
    pure_version = bool(api_version) and bool(_re.fullmatch(r'\d+\.\d+(?:\.\d+)?(?:-[\w.-]+)?', api_version))
    api_ok = (
        api_version.startswith('isaacsim_rpc.v1')
        or (pure_version and not supported.isdisjoint(expected_shapes))
    )
    if not api_ok:
        issues.append(_service_issue('service_contract_mismatch',
            f'Isaac Sim reports api_version={api_version!r}, supported_shapes={sorted(supported)!r}; '
            f'BenchForge requires {PROTOCOL} (SubmitJob/GetJob/DownloadArtifact/AckJob/CancelJob).'))
    return issues


# 只读核对场景契约与实时服务健康状态，不提交、不下载。
def inspect_live_capture_service(program, settings, *, scope=None):
    scope = scope or capture_scope(program, None)
    issues = capture_service_issues(program, settings, scope)
    client = capture_service_client({'providers': {ISAAC_PROVIDER: settings}})
    try:
        health = document(client.health())
    except grpc.RpcError as error:
        issues.append(_service_issue('service_unavailable',
            f'Isaac Sim gRPC health check failed: {error.details() or error.code().name}'))
    else:
        issues.extend(health_issues(health))
    finally:
        client.close()
    return issues
