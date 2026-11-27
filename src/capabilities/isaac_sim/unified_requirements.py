"""Admission checks for the isaacsim_rpc.v1 SceneProgram adapter.

Only geometry and lighting that the wire contract can express are admitted.
Unsupported requirements are reported together before any network call; nothing
is silently substituted, scaled or exported.
"""
from capabilities.scene_authoring.catalog.native import ASSETS
from capabilities.scene_authoring.catalog.native_reference import native_references
from capabilities.scene_authoring.contracts.scene_program import APPEARANCE_FIELDS
from capabilities.isaac_sim.scene_requirements import UnsupportedScene


# 检查场景对适配器的要求，遇到不支持项即阻止执行。
def validate_scene_requirements(program, settings=None):
    _require_supported(scene_requirement_issues(program, settings=settings))


# 原生资产依赖上传与 retained session；v2 契约不提供这些能力。
def native_reference_issues(program, settings=None):
    if not isinstance(program, dict):
        return []
    return [_issue(f'/objects/{index}/native_asset',
                   'Native asset imports require persistent scene operations on this endpoint',
                   obj.get('id'))
            for index, obj in native_references(program)]


# 校验场景中的原生资产引用，并将发现的问题作为异常返回。
def validate_native_references(program, settings=None):
    _require_supported(native_reference_issues(program, settings))


# 仅保留列表形式的声明，其他值视为空以便继续收集独立问题。
def _records(value):
    return value if isinstance(value, list) else []


# 构造带字段路径、对象标识和阻断级别的能力不支持问题。
def _issue(path, message, object_id=None):
    return {'code': 'unsupported_capture_requirement', 'path': path, 'message': message,
            'basis': 'isaacsim_rpc.v1', 'severity': 'error', 'blocking': True,
            **({'object_ids': [object_id]} if isinstance(object_id, str) else {})}


# 收集场景要求中的适配器限制，不把动作声明隐式转换为原生命令。
def scene_requirement_issues(program, *, settings=None):
    """Actions have no implicit SceneProgram-to-native command mapping."""
    if not isinstance(program, dict):
        return []
    field = 'interactions' if 'interactions' in program else 'affordances'
    return native_reference_issues(program, settings) + [_issue(f'/{field}/{index}',
                   'This SceneProgram action requires native session commands and interaction '
                   'receipts. The shared snapshot request only runs forward simulation.',
                   row.get('object_id'))
            for index, row in enumerate(_records(program.get(field, [])))
            if isinstance(row, dict) and row.get('action') in ('apply_force', 'robot_push', 'record_trajectory')]


# 校验三个正数尺寸是否均匀，仅用于球体半径的忠实映射。
def _uniform(size, path, identity, issues):
    if (isinstance(size, list) and len(size) == 3
            and all(type(value) in (int, float) for value in size)
            and max(abs(size[0] - size[1]), abs(size[1] - size[2])) > 1e-6):
        issues.append(_issue(path, 'sphere requires a uniform declared size so radius '
                             'can be expressed faithfully', identity))


# 报告 BodySpec 无法表达的材质外观字段，避免静默丢弃渲染参数。
def _appearance_issues(appearance, path, identity, issues):
    if not isinstance(appearance, dict):
        return
    for key in sorted(set(appearance) & APPEARANCE_FIELDS):
        issues.append(_issue(f'{path}/{key}',
            f'isaacsim_rpc.v1 BodySpec has no appearance {key!r} mapping; only color/base_color '
            'is expressible in a shared snapshot', identity))


# 无需编译或连接 GPU 即可收集采集适配器的独立限制。
def capture_requirement_issues(program, assets_root=None, settings=None):
    """Collect independent adapter limitations without contacting the service."""
    issues = scene_requirement_issues(program, settings=settings)
    if not isinstance(program, dict):
        return issues
    for index, obj in enumerate(_records(program.get('objects'))):
        if not isinstance(obj, dict):
            continue
        path, identity, kind = f'/objects/{index}', obj.get('id'), obj.get('kind')
        if not isinstance(kind, str):
            continue
        _appearance_issues(obj.get('appearance'), path + '/appearance', identity, issues)
        if 'cast_shadows' in obj:
            issues.append(_issue(path + '/cast_shadows',
                'Per-object shadow controls require persistent scene authoring', identity))
        if kind == 'sphere':
            _uniform(obj.get('size'), path + '/size', identity, issues)
        elif kind == 'composite':
            parts = obj.get('parts') or []
            if not parts:
                issues.append(_issue(path + '/parts', 'composite needs at least one part', identity))
            for part_index, part in enumerate(_records(parts)):
                if not isinstance(part, dict):
                    continue
                _appearance_issues(part.get('appearance'),
                                   f'{path}/parts/{part_index}/appearance', identity, issues)
                shape = part.get('shape')
                if shape not in ('box', 'sphere'):
                    issues.append(_issue(f'{path}/parts/{part_index}/shape',
                        f'part shape {shape!r} cannot be expressed as cube or sphere; '
                        'cylinder and other shapes are not supported', identity))
                elif shape == 'sphere':
                    _uniform(part.get('size'), f'{path}/parts/{part_index}/size', identity, issues)
        elif kind == 'container':
            continue
        elif kind == 'box':
            continue
        elif kind == 'cylinder':
            issues.append(_issue(path + '/kind',
                'cylinder has no BodySpec representation; use box, sphere, composite or mesh',
                identity))
        elif kind == 'mesh':
            asset_id = obj.get('asset_id')
            if not isinstance(asset_id, str) or not asset_id:
                issues.append(_issue(path + '/asset_id', 'mesh needs a registered asset_id', identity))
            elif settings is not None and not (settings.get('mesh_assets') or {}).get(asset_id):
                issues.append(_issue(path + '/asset_id',
                    f'Mesh {asset_id!r} requires its registered mesh_assets binding or persistent USD import', identity))
        elif kind == 'generated':
            issues.append(_issue(path + '/kind',
                'Generated assets require persistent USD import to preserve authored materials', identity))
        elif kind == 'native':
            continue  # Already reported by native_reference_issues.
        elif kind in ASSETS:
            issues.append(_issue(path + '/kind',
                f'Native catalog asset {kind!r} requires persistent scene import', identity))
        else:
            issues.append(_issue(path + '/kind', f'unsupported geometry kind {kind!r}', identity))
    for index, material in enumerate(_records(program.get('materials'))):
        if not isinstance(material, dict):
            continue
        for key in sorted((set(material) - {'id', 'name', 'base_color'}) & APPEARANCE_FIELDS):
            issues.append(_issue(f'/materials/{index}/{key}',
                f'isaacsim_rpc.v1 BodySpec has no material {key!r} mapping; only base_color '
                'is expressible in a shared snapshot'))
        _appearance_issues(material.get('appearance'), f'/materials/{index}/appearance',
                           None, issues)
    environment = program.get('render_environment')
    if isinstance(environment, dict):
        if isinstance(environment.get('exposure'), (int, float)) and environment['exposure'] != 0:
            issues.append(_issue('/render_environment/exposure',
                'isaacsim_rpc.v1 has no global exposure control; do not silently discard exposure'))
        spp = environment.get('samples_per_pixel', (settings or {}).get('samples_per_pixel', 32))
        if isinstance(spp, (int, float)) and spp < 1:
            issues.append(_issue('/render_environment/samples_per_pixel',
                'samples_per_pixel must be positive'))
    for index, light in enumerate(_records(program.get('lights'))):
        if not isinstance(light, dict) or light.get('enabled') is False:
            continue
        path = f'/lights/{index}'
        if light.get('kind') not in ('environment', 'area'):
            issues.append(_issue(path + '/kind', 'isaacsim_rpc.v1 maps only constant environment '
                                 'and rectangular area lights; other kinds have no LightingSpec'))
        for key in ('environment_id', 'temperature', 'cast_shadows'):
            if key in light:
                issues.append(_issue(path + '/' + key,
                                     f'isaacsim_rpc.v1 LightingSpec has no light {key} mapping'))
    return issues


# 把全部不支持项组合为保留结构化问题的异常。
def _require_supported(issues):
    if issues:
        raise UnsupportedScene('\n'.join(row['path'] + ': ' + row['message'] for row in issues),
                               issues=issues)


# 在进入采集前统一拒绝不支持的场景声明。
def validate_capture_requirements(program, assets_root, settings):
    _require_supported(capture_requirement_issues(program, assets_root, settings))


def unified_service_error(message, *, path='', retry=False):
    issue = {'code': 'service_not_ready' if retry else 'service_contract_mismatch',
             'path': path, 'message': message, 'severity': 'error', 'blocking': True,
             'basis': 'SimulationService.Health', 'repair_owner': 'service_configuration_or_adapter',
             'retry_unchanged': retry}
    return UnsupportedScene(message, issues=[issue])
