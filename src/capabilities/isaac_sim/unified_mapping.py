"""Map a validated SceneProgram onto the isaacsim_rpc.v1 SimulateRequest.

Only geometry the wire contract can express faithfully is submitted. Anything
else raises UnsupportedScene before any network call; nothing is silently
substituted or scaled.
"""
from __future__ import annotations

import json
import math

from capabilities.isaac_sim.unified_requirements import UnsupportedScene, capture_requirement_issues, unified_service_error
from capabilities.scene_authoring.catalog.native import ASSETS
from capabilities.scene_authoring.geometry.support_graph import objects_in_support_order


DEFAULT_SIMULATION = {'steps': 180, 'dt': 1 / 60, 'gravity': 9.81, 'ground': False,
                      'capture_every_steps': None, 'capture_target_frames': 24}
DEFAULT_RENDER = {'fov_degrees': 50.0, 'seed': 0, 'image_size': 768, 'samples_per_pixel': 32}
PROTOCOL = 'isaacsim_rpc.v1.SimulationService'


# 构造带字段路径与对象标识的阻断问题。
def _issue(path, message, object_id=None):
    return {'code': 'unsupported_capture_requirement', 'path': path, 'message': message,
            'basis': 'isaacsim_rpc.v1', 'severity': 'error', 'blocking': True,
            **({'object_ids': [object_id]} if isinstance(object_id, str) else {})}


def _vec(values):
    return {'x': float(values[0]), 'y': float(values[1]), 'z': float(values[2])}


def _quaternion_yaw(degrees):
    half = math.radians(float(degrees)) / 2
    return {'x': 0.0, 'y': 0.0, 'z': math.sin(half), 'w': math.cos(half)}


def _rotate_xy(offset, yaw_deg):
    angle = math.radians(float(yaw_deg))
    cosine, sine = math.cos(angle), math.sin(angle)
    return [offset[0] * cosine - offset[1] * sine,
            offset[0] * sine + offset[1] * cosine,
            offset[2]]


# 按支撑依赖计算对象声明的世界中心与顶面高度，不读取网格内容。
def placements(program):
    by_id = {}
    for obj in objects_in_support_order(program['objects']):
        size = [float(value) for value in obj['size']]
        support_top = 0.0 if obj['support'] == 'ground' else by_id[obj['support']]['top']
        bottom = support_top + float(obj['base_z'])
        center = [float(obj['xy'][0]), float(obj['xy'][1]), bottom + size[2] / 2]
        by_id[obj['id']] = {'center': center, 'size': size, 'bottom': bottom,
                            'top': bottom + size[2]}
    return by_id


# 解析对象或部件的显示颜色，仅使用协议能表达的基础色。
def body_color(program, obj, part=None):
    if isinstance(part, dict) and part.get('color') is not None:
        return [float(value) for value in part['color']]
    named_id = (part or {}).get('material_id') or obj.get('material_id')
    named = next((item for item in program.get('materials', [])
                  if item.get('id') == named_id), {})
    if named.get('base_color') is not None:
        return [float(value) for value in named['base_color']]
    for source in (part or {}, obj):
        appearance = source.get('appearance') or {}
        if appearance.get('base_color') is not None:
            return [float(value) for value in appearance['base_color']]
    if obj.get('color') is not None:
        return [float(value) for value in obj['color']]
    return [.7, .7, .7]


def _body(identity, shape, position, yaw, color, *, size=None, radius=None,
          asset=None, mass=None):
    body = {'id': identity, 'shape': shape, 'position': _vec(position),
            'orientation_xyzw': _quaternion_yaw(yaw), 'color': _vec(color)}
    if size is not None:
        body['size'] = _vec(size)
    if radius is not None:
        body['radius'] = float(radius)
    if asset is not None:
        body['asset'] = asset
    if mass is not None:
        body['mass'] = float(mass)
    return body


# 容器外壳按现有拆分规则生成五块盒壁。
def _container_parts(size, color):
    x, y, z = size
    thickness = min(.008, min(size) / 8)
    return [{'shape': 'box', 'size': dims, 'offset': offset, 'color': color}
            for dims, offset in [([x, y, thickness], [0, 0, -(z - thickness) / 2]),
                ([thickness, y, z - thickness], [-(x - thickness) / 2, 0, thickness / 2]),
                ([thickness, y, z - thickness], [(x - thickness) / 2, 0, thickness / 2]),
                ([x - 2 * thickness, thickness, z - thickness], [0, -(y - thickness) / 2, thickness / 2]),
                ([x - 2 * thickness, thickness, z - thickness], [0, (y - thickness) / 2, thickness / 2])]]


# 把单个对象（或复合对象的部件）转换为一个或多个 BodySpec。
def _object_bodies(obj, index, row, program, settings, issues):
    identity, kind, yaw = obj['id'], obj['kind'], float(obj.get('yaw_deg', 0))
    path = f'/objects/{index}'
    color = body_color(program, obj)
    mass = float(obj.get('mass_kg', 0)) if obj.get('dynamic') else None
    if kind == 'box':
        if any(not .001 <= edge <= 100 for edge in row['size']):
            issues.append(_issue(path + '/size', 'Shared simulation cube edges must be within .001..100 meters', identity))
        return [_body(identity, 'cube', row['center'], yaw, color,
                      size=row['size'], mass=mass)]
    if kind == 'sphere':
        size = row['size']
        if not .001 <= size[0] / 2 <= 100:
            issues.append(_issue(path + '/size', 'Shared simulation sphere radius must be within .001..100 meters', identity))
        if max(abs(size[0] - size[1]), abs(size[1] - size[2])) > 1e-6:
            issues.append(_issue(path + '/size',
                'sphere requires a uniform declared size so radius can be expressed faithfully',
                identity))
            return []
        return [_body(identity, 'sphere', row['center'], yaw, color,
                      radius=size[0] / 2, mass=mass)]
    if kind in ('composite', 'container'):
        parts = (_container_parts(row['size'], color) if kind == 'container'
                 else obj.get('parts') or [])
        if not parts:
            issues.append(_issue(path + '/parts',
                                 'composite needs at least one part', identity))
            return []
        bodies = []
        distributed = (mass / len(parts)) if mass is not None else None
        for part_index, part in enumerate(parts):
            shape = part.get('shape')
            if shape not in ('box', 'sphere'):
                issues.append(_issue(f'{path}/parts/{part_index}/shape',
                    f'part shape {shape!r} cannot be expressed as cube or sphere; '
                    'cylinder and other shapes are not supported', identity))
                continue
            position = [row['center'][axis] + _rotate_xy(part.get('offset', [0, 0, 0]), yaw)[axis]
                        for axis in range(3)]
            part_size = [float(value) for value in part['size']]
            part_color = body_color(program, obj, part)
            kwargs = ({'size': part_size} if shape == 'box'
                      else {'radius': part_size[0] / 2})
            if shape == 'sphere' and max(abs(part_size[0] - part_size[1]),
                                         abs(part_size[1] - part_size[2])) > 1e-6:
                issues.append(_issue(f'{path}/parts/{part_index}/size',
                    'sphere part requires a uniform declared size', identity))
                continue
            bodies.append(_body(f'{identity}__part{part_index}', 'cube' if shape == 'box' else 'sphere',
                                position, yaw, part_color, mass=distributed, **kwargs))
        return bodies
    if kind == 'cylinder':
        issues.append(_issue(path + '/kind',
            'cylinder has no BodySpec representation; use box, sphere, composite or mesh', identity))
        return []
    if kind == 'mesh':
        asset_id = obj.get('asset_id')
        binding = (settings.get('mesh_assets') or {}).get(asset_id)
        if not isinstance(binding, dict) or not binding.get('asset_id') or not binding.get('bounds_m'):
            issues.append(_issue(path + '/asset_id', 'Registered mesh needs its portable ID and baked bounds', identity))
            return []
        low, high = binding['bounds_m']
        if any(abs((b - a) - requested) > 1e-5 for a, b, requested in zip(low, high, row['size'])):
            issues.append(_issue(path + '/size', 'Authored mesh scaling requires retained USD geometry', identity))
            return []
        offset = [(a + b) / 2 for a, b in zip(low, high)]
        position = [a - b for a, b in zip(row['center'], _rotate_xy(offset, yaw))]
        return [_body(identity, 'mesh', position, yaw, color, mass=mass) | {'asset_id': binding['asset_id']}]
    if kind in ASSETS:
        issues.append(_issue(path + '/kind',
            f'built-in native asset {kind!r} is not supported by the v2 body contract; '
            'pre-stage a mesh.npz and use kind=mesh with mesh_assets', identity))
        return []
    if kind in ('native', 'generated'):
        issues.append(_issue(path + '/kind',
            f'{kind} objects require upload/native sessions that the v2 contract does not provide',
            identity))
        return []
    issues.append(_issue(path + '/kind',
                         f'unsupported geometry kind {kind!r}', identity))
    return []


# 把 SceneProgram 光源映射到 LightingSpec；无法表达的灯型明确拒绝。
def _lighting(program, issues):
    lights = [light for light in program.get('lights') or [{'kind': 'environment', 'intensity': 1}]
              if light.get('enabled', True)]
    dome, areas = None, []
    for index, light in enumerate(lights):
        kind, path = light.get('kind'), f'/lights/{index}'
        if kind == 'environment':
            if dome is not None:
                issues.append(_issue(path, 'only one environment light can map to the dome light'))
                continue
            dome = {'color': _vec(light.get('color', [1, 1, 1])), 'intensity': 1.0}
            if light.get('intensity') is not None:
                dome['intensity'] = float(light['intensity'])
        elif kind == 'area':
            position = [float(value) for value in light['position']]
            direction = light.get('direction', [0, 0, -1])
            size = light.get('size', [1, 1])
            area = {'color': _vec(light.get('color', [1, 1, 1])), 'intensity': 1.0,
                    'origin': _vec(position),
                    'target': _vec([position[axis] + float(direction[axis]) for axis in range(3)]),
                    'width': float(size[0]), 'height': float(size[1])}
            if light.get('intensity') is not None:
                area['intensity'] = float(light['intensity'])
            areas.append(area)
        else:
            issues.append(_issue(path + '/kind',
                f'light kind {kind!r} has no LightingSpec mapping; use environment or area lights'))
    # Both backends otherwise add their own dome and key to the authored scene.
    lighting = {'dome': {'intensity': 0.0}, 'key': {'intensity': 0.0}}
    if dome is not None:
        lighting['dome'] = dome
    if areas:
        if len(areas) > 9:
            issues.append(_issue('/lights', 'Shared simulation accepts one key and at most eight fill lights'))
        lighting['key'] = areas[0]
        if len(areas) > 1:
            lighting['fills'] = areas[1:]
    return lighting or None


# 由相机世界位姿重建协议可表达的方位角、仰角、距离和目标点。
def camera_view(index, camera, issues):
    position = [float(value) for value in camera['position']]
    target = [float(value) for value in camera['target']]
    delta = [position[axis] - target[axis] for axis in range(3)]
    distance = math.sqrt(sum(value * value for value in delta))
    if distance <= 1e-9:
        issues.append(_issue(f'/cameras/{index}', 'camera position and target must differ'))
        return None
    elevation = math.degrees(math.asin(max(-1.0, min(1.0, delta[2] / distance))))
    azimuth = math.degrees(math.atan2(delta[1], delta[0]))
    return {'index': index, 'camera': camera, 'target': target,
            'azimuth': azimuth, 'elevation': elevation, 'distance': distance}


# 解析 capture_options 与配置，得到固定/自适应采样间隔与显式帧。
def capture_sampling(steps, settings, scope, issues):
    simulation = {**DEFAULT_SIMULATION, **(settings.get('simulation') or {})}
    options = scope.get('capture_frames') or {}
    if not options and not (settings.get('simulation') or {}).get('capture_every_steps'):
        return {'steps': steps, 'every': None, 'frames': [steps], 'target_frames': 1}
    frames = [int(value) for value in options.get('frames') or []]
    every = options.get('every')
    if frames:
        frames = sorted(set(frames))
        path = '/capture_options/frames'
    elif every is not None:
        every = int(every)
        frames = [steps]
        path = '/capture_options/every'
    else:
        configured = simulation.get('capture_every_steps')
        every = (int(configured) if configured is not None else
                 max(1, math.ceil(steps / max(1, int(simulation.get('capture_target_frames', 24))))))
        frames = [steps]
        path = '/capture_options'
    if any(value < 1 or value > steps for value in frames):
        issues.append(_issue(path, f'explicit frames must be within 1..{steps}'))
    if len(frames) > 1200:
        issues.append(_issue(path, 'capture accepts at most 1200 explicit frames'))
    if every is not None and not 1 <= every <= steps:
        issues.append(_issue(path, f'capture interval must be within 1..{steps}'))
    return {'steps': steps, 'every': every, 'frames': frames,
            'target_frames': int(simulation.get('capture_target_frames', 24))}


# 生成完整的仿真计划；任何无法忠实表达的要求汇总后一次性拒绝。
def simulation_plan(program, settings, scope):
    issues = []
    simulation = {**DEFAULT_SIMULATION, **(settings.get('simulation') or {})}
    render = {**DEFAULT_RENDER, **(settings.get('render') or {})}
    steps, dt = int(simulation['steps']), float(simulation['dt'])
    if not 1 <= steps <= 1200:
        issues.append(_issue('/simulation/steps', 'steps must be within 1..1200'))
    if not 0.0001 <= dt <= 0.02:
        issues.append(_issue('/simulation/dt', 'dt must be within 0.0001..0.02'))
    size = int(render.get('image_size', 768))
    spp = int(render.get('samples_per_pixel', 32))
    if size < 1 or spp < 1:
        issues.append(_issue('/render', 'image_size and samples_per_pixel must be positive'))
    rows = placements(program)
    bodies, entities = [], {}
    for index, obj in enumerate(program['objects']):
        row = rows[obj['id']]
        produced = _object_bodies(obj, index, row, program, settings, issues)
        bodies.extend(produced)
        entities[obj['id']] = {'id': obj['id'], 'label': obj.get('label'),
                               'kind': obj['kind'], 'dynamic': bool(obj.get('dynamic')),
                               'support': obj.get('support'), 'center': row['center'],
                               'size': row['size'], 'mass_kg': obj.get('mass_kg'),
                               'yaw_deg': float(obj.get('yaw_deg', 0)),
                               'body_ids': [body['id'] for body in produced]}
        if obj['kind'] == 'mesh' and produced:
            low, high = settings['mesh_assets'][obj['asset_id']]['bounds_m']
            entities[obj['id']]['asset_id'] = produced[0]['asset_id']
            entities[obj['id']]['mesh_local_center'] = [(a + b) / 2 for a, b in zip(low, high)]
    views = [view for index in scope.get('view_indices', [])
             for view in [camera_view(index, program['cameras'][index], issues)] if view]
    sampling = capture_sampling(steps, settings, scope, issues)
    lighting = _lighting(program, issues)
    if not 1 <= len(bodies) <= 256:
        issues.append(_issue('/objects', 'Shared simulation accepts 1..256 rigid bodies per world'))
    if issues:
        raise UnsupportedScene('\n'.join(row['path'] + ': ' + row['message'] for row in issues),
                               issues=issues)
    seed_value = render.get('seed')
    render_payload = {'size': size, 'spp': spp,
                      'fov_degrees': float(render.get('fov_degrees', 50))}
    if seed_value is not None:
        # 配置允许 seed=null（让服务端自动选种子），此时不要写入请求。
        render_payload['seed'] = int(seed_value)
    return {'bodies': bodies, 'entities': entities, 'views': views, 'steps': steps, 'dt': dt,
            'gravity': float(simulation['gravity']), 'ground': bool(simulation['ground']),
            'capture': sampling, 'lighting': lighting, 'render': render_payload}


def simulation_request(plan):
    simulation = {key: plan[key] for key in ('bodies', 'steps', 'dt', 'gravity', 'ground')}
    if not plan['views']:
        return simulation
    render = {key: value for key, value in plan['render'].items() if key in ('size', 'spp', 'seed')}
    render['views'] = []
    for view in plan['views']:
        camera = view['camera']
        fov = math.degrees(2 * math.atan(camera.get('horizontal_aperture_mm', 36) /
                                         (2 * camera.get('focal_length_mm', 24))))
        up = camera.get('up', [0, 1, 0] if abs((camera['target'][2] - camera['position'][2]) /
                                                  view['distance']) > .999 else [0, 0, 1])
        render['views'].append({'id': f"view_{view['index']}", 'eye': _vec(camera['position']),
                                'target': _vec(camera['target']), 'up': _vec(up), 'fov_y_degrees': fov})
    if plan.get('lighting'):
        render['lighting'] = plan['lighting']
    capture = {'frames': plan['capture']['frames']}
    if plan['capture']['every'] is not None:
        capture['every'] = plan['capture']['every']
    return {**simulation, 'render': render, 'capture': capture}


def snapshot_settings(program, settings):
    from service_access.configuration import isaac_unified_settings
    resolved = isaac_unified_settings(settings)
    environment = program.get('render_environment') or {}
    resolved['render'] = {'image_size': resolved.get('image_size', 1024),
                          'samples_per_pixel': max(resolved.get('samples_per_pixel', 256),
                                                   environment.get('samples_per_pixel', 0))}
    return resolved


def select_snapshot_plan(program, settings, scope, health, *, assets_root=None, preview=False):
    """Return a faithful snapshot plan or select the advertised persistent scene service."""
    config = snapshot_settings(program, settings)
    views = scope['view_indices']
    capabilities = set(health.capabilities)
    limits = [('image_size', health.max_image_size), ('samples_per_pixel', health.max_samples_per_pixel)]
    for field, limit in limits:
        if views and limit and config['render'][field] > limit:
            raise unified_service_error(f"{health.engine} {field} limit is {limit}, requested {config['render'][field]}",
                                        path='/render/' + field)
    renderer = (program.get('render_environment') or {}).get('renderer')
    if views and renderer:
        actual = 'RaytracedLighting' if health.native_rtx else 'PathTracing'
        if actual != renderer:
            raise unified_service_error(f'{health.engine} provides {actual}; scene requires {renderer}',
                                        path='/render_environment/renderer')
    issues = capture_requirement_issues(program, settings=config)
    source_materials = {}
    for index, obj in enumerate(program['objects']):
        asset = obj.get('asset_id')
        if obj['kind'] == 'mesh' and asset and assets_root is not None and asset not in source_materials:
            directory = assets_root / asset
            # Registered NPZ can carry vertex channels, but cannot transport GLB/UV materials.
            rich = (directory / 'asset.glb').is_file()
            if not rich and asset in config.get('mesh_assets', {}) and (directory / 'mesh.json').is_file():
                source = json.loads((directory / 'mesh.json').read_text(encoding='utf-8'))
                rich = any(source.get(key) for key in ('materials', 'material_textures', 'texcoords',
                                                       'face_materials', 'appearance'))
            source_materials[asset] = rich
        if (obj['kind'] not in ('box', 'sphere', 'mesh') or obj.get('physics') or obj.get('mesh_transform')
                or obj.get('render_representation') == 'gaussian' or source_materials.get(asset) or obj.get('kind') == 'mesh'
                and (obj.get('material_id') or obj.get('appearance') or 'color' in obj)):
            issues.append({'path': f'/objects/{index}',
                           'message': f"{obj['id']} requires authored USD geometry, physics or material bindings"})
    if preview or settings.get('interactive_preview') and views:
        issues.append({'path': '/interactive_preview', 'message': 'Interactive preview requires a persistent session'})
    if views:
        missing = {'multiview_frames', 'metric_depth', 'semantic_masks'} - capabilities
        if not capabilities.intersection({'instance_ids', 'instance_masks'}):
            missing.add('instance_ids')
        if missing:
            issues.append({'path': '/cameras', 'message': 'Missing capture capabilities: ' + ', '.join(sorted(missing))})
        if len(views) > health.max_views:
            issues.append({'path': '/cameras', 'message': f'{len(views)} cameras exceed the shared snapshot limit {health.max_views}'})
    if any(obj['kind'] == 'mesh' for obj in program['objects']) and 'registered_mesh_npz' not in capabilities:
        issues.append({'path': '/objects', 'message': 'Registered mesh BodySpec support is unavailable'})
    if not issues:
        try:
            plan = simulation_plan(program, config, scope)
        except UnsupportedScene as error:
            issues = error.issues
        else:
            missing_shapes = {body['shape'] for body in plan['bodies']} - set(health.supported_shapes)
            if not health.supported_shapes or not missing_shapes:
                return plan
            issues.append({'path': '/objects', 'message': 'Unsupported body shapes: ' + ', '.join(sorted(missing_shapes))})
    if scope.get('capture_frames'):
        raise unified_service_error('Process frames require a supported shared snapshot. ' +
            '; '.join(row['message'] for row in issues), path='/capture_options')
    if 'native_operations' in capabilities:
        return None
    message = f'{health.engine} does not provide persistent scene operations. ' + '; '.join(
        f"{issue['path']}: {issue['message']}" for issue in issues)
    raise unified_service_error(message, path=issues[0]['path'])
