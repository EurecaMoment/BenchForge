"""Compare adapter requests with live service fields before any scene is created."""

import time

from capabilities.isaac_sim.scene_requirements import UnsupportedScene
from service_access.request_context import check_cancelled


def native_import_arguments(item, identity):
    """统一生成原生资产导入参数，预检与执行共享质量、材质和坐标轴声明。"""
    obj, binding = item['object'], item['source']
    return {'session_id': identity, 'path': item['path'],
            'source': {key: binding[key] for key in ('asset_id', 'relative_path')},
            'fix_base': binding.get('fix_base', not obj['dynamic']),
            'mass_kg': obj['mass_kg'], 'physics': obj.get('physics', {}),
            'source_up_axis': item['catalog'].get('source_up_axis', 'Z')}


def request_fields(value):
    """提取请求的字段树；值域由原生服务负责，不在适配器另造更严格的规则。"""
    if isinstance(value, dict):
        return {key: request_fields(child) for key, child in value.items()}
    if isinstance(value, list):
        return [request_fields(child) for child in value]
    return {}


def native_edit_arguments(obj, path, identity, *, center, scale):
    """共用原生对象的位姿和语义编辑结构，避免预检与实际请求各维护一套字段。"""
    return {'session_id': identity, 'changes': [
        {'op': 'transform', 'path': path, 'translation': center,
         'rotation_deg': [0, 0, obj.get('yaw_deg', 0)], 'scale': scale},
        {'op': 'set', 'path': path, 'attribute': 'a100sim:semanticLabel',
         'type': 'string', 'value': obj['label']}]}


def unsupported_fields(schema, fields, prefix=''):
    """只报告实时接口明确禁止的字段，开放对象和未声明限制的对象原样兼容。"""
    if not isinstance(schema, dict) or any(key in schema for key in ('$ref', 'oneOf', 'anyOf', 'allOf')):
        # 组合合同需要值域参与分支选择，字段预检不能擅自选择更严格的分支。
        return []
    if isinstance(fields, list):
        return [issue for index, item in enumerate(fields)
                for issue in unsupported_fields(schema.get('items', {}), item, prefix + '/' + str(index))]
    properties = schema.get('properties', {})
    issues = []
    for key, children in fields.items():
        path = prefix + '/' + key
        if key not in properties:
            if schema.get('additionalProperties') is False:
                issues.append(path)
            continue
        if children and isinstance(properties[key], dict):
            issues.extend(unsupported_fields(properties[key], children, path))
    return issues


def capture_service_issues(capabilities, compiled, scope):
    """批量对账整个采集流程的接口和字段，覆盖所有原生本体并保留用户物理要求。"""
    expected = [
        ('asset.import', '', {'source': {'asset_id': {}, 'relative_path': {}}, 'path': {}}),
        ('physics.step', '', dict.fromkeys(('session_id', 'steps', 'dt'), {})),
        ('world.observe', '', dict.fromkeys(('session_id', 'bodies', 'imu', 'contacts'), {})),
        ('scene.inspect', '', dict.fromkeys(('session_id', 'limit', 'cursor'), {})),
        ('scene.close', '', dict.fromkeys(('session_id', 'delete'), {})),
    ]
    if scope['view_indices']:
        expected.append(('scene.capture', '/cameras', dict.fromkeys(
            ('session_id', 'cameras', 'size', 'spp', 'annotations'), {})))
    if compiled['imports'] or scope['scene_export_requested']:
        expected.append(('scene.export', '', dict.fromkeys(('session_id', 'format'), {})))
    for item in compiled['imports']:
        expected.append(('asset.import', '/objects/' + item['object']['id'],
                         request_fields(native_import_arguments(item, 'retained-session'))))
        # 此处仅取得字段结构；位置/缩放占位值不用于执行，也不代表几何验收。
        expected.append(('scene.edit', '/objects/' + item['object']['id'], request_fields(
            native_edit_arguments(item['object'], item['path'], 'retained-session',
                                  center=[0, 0, 0], scale=[1, 1, 1]))))
    published = {row['name']: row for row in capabilities.get('capabilities', [])}
    issues = []
    for operation, path, fields in expected:
        name = 'extension.' + operation
        contract = published.get(name, {})
        availability = contract.get('availability', 'MISSING')
        schema = contract.get('details', {}).get('parameter_schema', {})
        unsupported = unsupported_fields(schema, fields)
        if availability == 'AVAILABLE' and not unsupported:
            continue
        if availability == 'UNAVAILABLE' and not unsupported:
            issues.append({'code': 'service_not_ready', 'path': path, 'operation': name,
                'message': f"{name}: {contract.get('reason') or 'worker unavailable'}. Recheck readiness before capture.",
                'basis': 'live_isaac_sim_schema', 'severity': 'error', 'blocking': True,
                'repair_owner': 'service_configuration_or_adapter', 'retry_unchanged': True})
            continue
        issues.append({'code': 'service_contract_mismatch', 'path': path,
            'message': f'{name}: service availability={availability}' +
                       (f'; adapter fields rejected by live schema: {", ".join(unsupported)}' if unsupported else '') +
                       '. Correct the configured service or adapter; do not remove scene requirements or retry unchanged captures.',
            'basis': 'live_isaac_sim_schema', 'severity': 'error', 'blocking': True,
            'operation': name, 'unsupported_fields': unsupported,
            'repair_owner': 'service_configuration_or_adapter', 'retry_unchanged': False})
    return issues


def require_capture_service(capabilities, compiled, scope):
    """在任何上传或仿真提交之前一次性拒绝不兼容服务，并保留完整问题清单。"""
    issues = capture_service_issues(capabilities, compiled, scope)
    if issues:
        raise UnsupportedScene('\n'.join(row['message'] for row in issues), issues=issues)


def capture_capabilities(client):
    """发现失败按服务问题保留结构化原因，不能被修复队列误当作场景缺陷。"""
    import grpc
    from capabilities.isaac_sim.grpc.transport import document
    try:
        return document(client.call('GetCapabilities'))
    except grpc.RpcError as error:
        issue = {'code': 'service_unavailable', 'path': '', 'severity': 'error', 'blocking': True,
                 'basis': 'live_isaac_sim_schema', 'repair_owner': 'service_configuration_or_adapter',
                 'retry_unchanged': error.code() in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED),
                 'message': f'Configured Isaac Sim discovery failed: {error.code().name}; preserve scene requirements.'}
        raise UnsupportedScene(issue['message'], issues=[issue]) from error


def checked_service_issues(client, compiled, scope, *, stop=None, on_wait=None, delays=(2, 4, 8, 12)):
    """Recheck transient worker readiness before any mutation. Schema errors return immediately."""
    for delay in (*delays, None):
        check_cancelled(stop, 'Isaac readiness')
        try:
            issues = capture_service_issues(capture_capabilities(client), compiled, scope)
        except UnsupportedScene as error:
            issues = error.issues
        if not issues or delay is None or not all(issue.get('retry_unchanged') for issue in issues):
            return issues
        if on_wait:
            on_wait(issues[0]['message'], delay)
        for _ in range(delay):
            check_cancelled(stop, 'Isaac readiness')
            time.sleep(1)


def capture_service_client(config):
    """把连接配置错误归于服务，避免凭据或证书故障触发场景重写。"""
    from capabilities.isaac_sim.grpc.transport import A100Client
    try:
        return A100Client(config)
    except (ValueError, OSError) as error:
        issue = {'code': 'service_configuration_invalid', 'path': '', 'severity': 'error',
                 'blocking': True, 'basis': 'configured_isaac_sim_service',
                 'repair_owner': 'service_configuration_or_adapter', 'retry_unchanged': False,
                 'message': 'Configured Isaac Sim client could not initialize; preserve scene requirements.'}
        raise UnsupportedScene(issue['message'], issues=[issue]) from error


def inspect_live_capture_service(program, settings, *, scope=None):
    """只读发现实际配置服务的能力；不创建世界、上传资产或执行仿真。"""
    from capabilities.capture.capture_scope import capture_scope
    from capabilities.scene_authoring.catalog.native import ASSETS
    compiled = {'imports': []}
    for obj in program['objects']:
        kind = obj['kind']
        if kind == 'mesh' and obj.get('asset_id') in ASSETS:
            kind = obj['asset_id']
        if kind == 'native' or kind in ASSETS:
            binding = obj['native_asset'] if kind == 'native' else settings.get('asset_sources', {}).get(kind)
            if binding:
                compiled['imports'].append({'object': obj, 'source': binding,
                    'path': '/World/Scene/Objects/' + obj['id'],
                    'catalog': {'source_up_axis': binding['source_up_axis']} if kind == 'native' else ASSETS[kind]})
    client = capture_service_client({'providers': {'IsaacSim4A100': settings}})
    try:
        # 预检尚未绑定输出选择，只核对必经步骤；执行前再核对实际相机和导出请求。
        if scope is None:
            scope = capture_scope(program, {'view_indices': [], 'export_scene': False})
        return checked_service_issues(client, compiled, scope, delays=(2,))
    except UnsupportedScene as error:
        return error.issues
    finally:
        client.close()
