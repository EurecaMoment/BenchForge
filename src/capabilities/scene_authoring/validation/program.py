"""Model proposals are data; only these bounded operations reach Isaac Sim."""
from capabilities.scene_authoring.contracts.scene_program import SCENE_V2_MAX_OBJECTS
from capabilities.scene_authoring.contracts.scene_program import SCENE_SCHEMAS
from capabilities.scene_authoring.contracts.scene_program import SCENE_MAX_ROOMS
from capabilities.scene_authoring.contracts.scene_program import SCENE_MAX_ZONES
from capabilities.scene_authoring.contracts.scene_program import SCENE_MAX_PORTALS
from capabilities.scene_authoring.contracts.scene_program import SCENE_MAX_ASSETS
from capabilities.scene_authoring.contracts.scene_program import LIGHT_KINDS
from capabilities.scene_authoring.contracts.scene_program import LIGHT_FIELDS
from capabilities.scene_authoring.contracts.scene_program import RENDER_ENVIRONMENT_FIELDS
from capabilities.scene_authoring.contracts.scene_program import RENDERERS
from capabilities.scene_authoring.contracts.scene_program import SPATIALTREE_RECORDING_FIELDS
from capabilities.scene_authoring.contracts.scene_program import APPEARANCE_FIELDS
import math
import re
from copy import deepcopy
from capabilities.scene_authoring.catalog.native import ASSETS
from capabilities.scene_authoring.catalog.native import NATIVE_MATERIALS
from capabilities.scene_authoring.catalog.native_reference import validate_native_reference
from capabilities.scene_authoring.catalog.environments import ENVIRONMENTS
# 条件不成立时抛出明确的合同校验错误。
def require(ok, message):
    if not ok: raise ValueError(message)
# 验证以字母或下划线开头、仅含字母数字下划线的场景标识。
def ident(value):
    require(isinstance(value, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value),
            f'invalid ID {value!r}: use letters, digits and underscores, starting with a letter or underscore')
    return value
# 验证固定长度数值列表，要求有限且落在指定闭区间内。
def numbers(value, length, lo, hi, context='vector'):
    label = 'invalid vector' if context == 'vector' else context
    require(isinstance(value, list) and len(value) == length, f'{label}: expected {length} numbers; got {value!r}')
    require(all(type(v) in (float, int) and math.isfinite(v) and lo <= v <= hi for v in value),
            f'{label}: expected finite numbers in [{lo}, {hi}]; got {value!r}')
# 验证三个正数尺寸，限制其最大范围。
def dimensions(value, context='size'):
    numbers(value, 3, 0, 1000, context)
    require(all(v > 0 for v in value), f'{context}: dimensions must be positive; got {value!r}')
# 检查外观字段白名单、数值范围及已登记纹理，拒绝未实现的效果声明。
def validate_appearance(appearance,context='appearance'):
    require(isinstance(appearance,dict),f'{context}: appearance must be an object')
    unknown=set(appearance)-APPEARANCE_FIELDS
    require(not unknown,f'{context}: unsupported appearance fields {sorted(unknown)}; allowed={sorted(APPEARANCE_FIELDS)}. Replace the complete appearance record with supported fields; generic opacity/transmission are not implemented. native_opacity_multiplier only works on explicitly registered capable native assets. Do not move unsupported fields into metadata and claim a rendering effect.')
    for key in ('roughness','metallic','specular','native_opacity_multiplier'):
        if key in appearance:numbers([appearance[key]],1,0,1)
    if 'ior' in appearance:numbers([appearance['ior']],1,1,3,'appearance.ior')
    if 'texture_id' in appearance:
        require(isinstance(appearance['texture_id'],str) and appearance['texture_id'] in NATIVE_MATERIALS,'texture_id must be an operator-registered material ID')
    if 'uv_scale_m' in appearance:numbers(appearance['uv_scale_m'],2,.01,100)
    if 'texture_tint' in appearance:numbers(appearance['texture_tint'],3,0,1,'appearance.texture_tint')
# 递归追加扩展元数据，同时保留已有记录的身份和内容约束。
def _merge_extension_value(base, added, path):
    """Append new metadata while retaining the identity of existing records."""
    if isinstance(base, dict) and isinstance(added, dict):
        merged = deepcopy(base)
        for key, value in added.items():
            merged[key] = _merge_extension_value(base[key], value, f'{path}.{key}') if key in base else deepcopy(value)
        return merged
    if isinstance(base, list) and isinstance(added, list):
        merged = deepcopy(base)
        by_id = {item['id']: item for item in base if isinstance(item, dict) and 'id' in item}
        for item in added:
            if isinstance(item, dict) and item.get('id') in by_id:
                require(item == by_id[item['id']], f'extension overwrites existing metadata: {path}.{item["id"]}')
            elif item not in merged:
                merged.append(deepcopy(item))
        return merged
    require(base == added, f'extension overwrites existing metadata: {path}')
    return deepcopy(base)
# 把规划器增量合并到场景副本，保持基础场景与原始提案不变。
def merge_scene_extension(base, added):
    """Merge a planner delta without mutating the base or the raw proposal."""
    require(isinstance(added, dict), 'scene must be an object')
    require(isinstance(added.get('objects'), list), 'extension needs new objects')
    original = {obj['id'] for obj in base['objects']}
    for obj in added['objects']:
        require(isinstance(obj, dict), 'invalid object')
        require(obj.get('id') not in original, 'extension overwrites existing entities; return only new objects')
    merged = deepcopy(base)
    for key, value in added.items():
        if key == 'objects':
            merged[key] = deepcopy(base[key]) + deepcopy(value)
        elif key in {'scene_id', 'title', 'cameras'}:
            merged[key] = deepcopy(value)
        elif key == 'schema':
            require(value in SCENE_SCHEMAS, 'unsupported scene schema')
            merged[key] = 'benchforge.scene/v2' if base[key].endswith('/v2') else value
        else:
            merged[key] = _merge_extension_value(base[key], value, key) if key in base else deepcopy(value)
    merged['schema'] = merged['schema']
    return merged
# 校验外力动作的动态目标、力大小、仿真步数及观察和接触约束。
def _validate_force_action(action, objects):
    required = {'id', 'action', 'object_id', 'force_newtons'}
    optional = {'duration_steps', 'observe_steps', 'min_displacement_m', 'max_displacement_m',
                'max_vertical_displacement_m', 'max_final_speed_m_s', 'max_initial_speed_m_s', 'max_drift_m', 'camera_id', 'recording', 'contact_object_ids'}
    require(required <= set(action) <= required | optional, 'invalid apply_force fields')
    require(action['object_id'] in objects and objects[action['object_id']]['dynamic'], 'apply_force needs a dynamic object')
    numbers(action['force_newtons'], 3, -1000, 1000)
    magnitude_sq = sum(value * value for value in action['force_newtons'])
    require(1e-12 < magnitude_sq <= 1000000, 'apply_force magnitude must be nonzero and <= 1000 N')
    for key, default in (('duration_steps', 30), ('observe_steps', 90)):
        value = action.get(key, default)
        require(type(value) is int and 1 <= value <= 600, f'{key} must be 1..600')
    contacts = action.get('contact_object_ids', [])
    require(isinstance(contacts, list) and all(isinstance(c, str) and c in objects and c != action['object_id'] for c in contacts)
            and len(set(contacts)) == len(contacts), 'contact_object_ids must name distinct other scene objects')
    for key in optional - {'duration_steps', 'observe_steps', 'camera_id', 'recording', 'contact_object_ids'}:
        if key in action:
            numbers([action[key]], 1, 0, 100)
    minimum = action.get('min_displacement_m', .02)
    maximum = action.get('max_displacement_m', 1)
    require(0 < minimum <= maximum, 'apply_force displacement bounds must satisfy 0 < min <= max')
# 校验可选字符串出现时非空且不超过指定长度。
def _optional_string(value, message='invalid string', max_len=200):
    require(isinstance(value, str) and 0 < len(value) <= max_len, message)
# 校验值为列表且条目数量不超过上限。
def _bounded_list(value, max_len, message):
    require(isinstance(value, list) and len(value) <= max_len, message)
# 验证主动启用的原生轨迹记录配置、采样和相关约束。
def _validate_spatialtree_recording(recording):
    """Validate the opt-in native trajectory recording settings."""
    require(isinstance(recording, dict), 'recording.spatialtree must be an object')
    require(set(recording) <= SPATIALTREE_RECORDING_FIELDS,
            'recording.spatialtree accepts scenario_id and split; image size and fps come from actual capture settings')
    if 'scenario_id' in recording:
        _optional_string(recording['scenario_id'], 'recording.spatialtree.scenario_id must be a non-empty string', 160)
    if 'split' in recording:
        require(isinstance(recording['split'], str) and recording['split'] in {'train', 'dev', 'test', 'sealed'},
                'recording.spatialtree.split must be train, dev, test or sealed')
# 校验从零步开始且严格递增的相机关键帧，位置与目标不能重合。
def _validate_camera_path(path):
    require(isinstance(path, list) and len(path) >= 2, 'recording.camera_path needs at least two keyframes')
    previous = -1
    for frame in path:
        require(isinstance(frame, dict) and set(frame) == {'step', 'position', 'target'},
                'camera path keyframes need step, position and target')
        require(type(frame['step']) is int and previous < frame['step'] <= 10000,
                'camera path steps must increase within 0..10000')
        numbers(frame['position'], 3, -1000, 1000, 'camera path position')
        numbers(frame['target'], 3, -1000, 1000, 'camera path target')
        require(sum((p-t)**2 for p,t in zip(frame['position'], frame['target'])) > 1e-8,
                'camera path position and target must differ')
        previous = frame['step']
    require(path[0]['step'] == 0, 'camera path must begin at step 0 relative to the first recording sample')
# 验证三维尺寸或含中心与尺寸的房间式边界声明。
def _validate_bounds(value, message='invalid bounds'):
    """Accept either [x, y, z] size or {center, size} room-style bounds."""
    if isinstance(value, list):
        dimensions(value)
        return
    require(isinstance(value, dict) and set(value) <= {'center', 'size'}, message)
    require({'center', 'size'} <= set(value), message)
    numbers(value['center'], 3, -1000, 1000)
    dimensions(value['size'])
# 检查场景扩展元数据结构，原生模拟器有效性仍由适配器后续验证。
def _validate_scene_extensions(p, checks):
    """Validate open-ended v2 scene metadata without constraining generators.

    These fields describe intent and provenance.  Isaac-specific validity is
    established later by the native adapter and remains separate from this
    lightweight contract.
    """
    object_rows = p.get('objects', []) if isinstance(p.get('objects'), list) else []
    object_ids = {o['id'] for o in object_rows if isinstance(o, dict) and isinstance(o.get('id'), str)}
    rooms = p.get('rooms', [])
    rooms_records = checks.records(rooms, '/rooms', SCENE_MAX_ROOMS, 'too many rooms')
    if not isinstance(rooms, list):
        rooms = []
    room_ids = set()
    for index, room in rooms_records:
        with checks.at(f'/rooms/{index}'):
            require(isinstance(room, dict), 'invalid room')
            require({'id', 'name', 'bounds'} <= set(room), 'room needs id, name and bounds')
            require(set(room) <= {'id', 'name', 'bounds', 'floor_z', 'representation', 'geometry', 'semantic_role'}, 'invalid room fields')
            ident(room['id']); require(room['id'] not in room_ids, 'duplicate room ID')
            _optional_string(room['name'], 'invalid room name')
            _validate_bounds(room['bounds'])
            if 'floor_z' in room:
                require(type(room['floor_z']) in (int, float) and math.isfinite(room['floor_z']) and -100 <= room['floor_z'] <= 100, 'invalid room floor')
            representation = room.get('representation', 'bounds_only')
            require(representation in {'bounds_only', 'explicit_objects', 'imported_geometry'}, 'invalid room representation')
            if 'semantic_role' in room: _optional_string(room['semantic_role'], 'invalid room semantic role')
            if representation == 'explicit_objects':
                geometry = room.get('geometry')
                require(isinstance(geometry, dict) and set(geometry) <= {'object_ids', 'notes'} and 'object_ids' in geometry, 'explicit room geometry needs object_ids')
                _bounded_list(geometry['object_ids'], SCENE_V2_MAX_OBJECTS, 'too many room geometry objects')
                require(all(item in object_ids for item in geometry['object_ids']), 'room geometry object must exist')
            elif 'geometry' in room:
                require(isinstance(room['geometry'], dict), 'invalid room geometry')
        if isinstance(room, dict) and isinstance(room.get('id'), str):
            room_ids.add(room['id'])

    zones = p.get('zones', [])
    zones_records = checks.records(zones, '/zones', SCENE_MAX_ZONES, 'too many zones')
    if not isinstance(zones, list):
        zones = []
    zone_ids = set()
    for index, zone in zones_records:
        with checks.at(f'/zones/{index}'):
            require(isinstance(zone, dict), 'invalid zone')
            require({'id', 'name', 'bounds'} <= set(zone), 'zone needs id, name and bounds')
            ident(zone['id']); require(zone['id'] not in zone_ids, 'duplicate zone ID')
            _optional_string(zone['name'], 'invalid zone name')
            _validate_bounds(zone['bounds'])
            if 'room_id' in zone:
                require(zone['room_id'] in room_ids, 'zone room must reference a room')
            if 'purpose' in zone:
                _optional_string(zone['purpose'], 'invalid zone purpose')
        if isinstance(zone, dict) and isinstance(zone.get('id'), str):
            zone_ids.add(zone['id'])

    portals = p.get('portals', [])
    portals_records = checks.records(portals, '/portals', SCENE_MAX_PORTALS, 'too many portals')
    if not isinstance(portals, list):
        portals = []
    portal_ids = set()
    for index, portal in portals_records:
        with checks.at(f'/portals/{index}'):
            require(isinstance(portal, dict), 'invalid portal')
            require({'id', 'kind', 'position', 'size'} <= set(portal), 'portal needs id, kind, position and size')
            ident(portal['id']); require(portal['id'] not in portal_ids, 'duplicate portal ID')
            _optional_string(portal['kind'], 'invalid portal kind', 40)
            numbers(portal['position'], 3, -1000, 1000); numbers(portal['size'], 3, .001, 100)
            for key in ('from_room', 'to_room'):
                if key in portal:
                    require(portal[key] in room_ids, 'portal room must reference a room')
            if 'passable' in portal: require(type(portal['passable']) is bool, 'invalid portal passability')
        if isinstance(portal, dict) and isinstance(portal.get('id'), str):
            portal_ids.add(portal['id'])

    assets = p.get('assets', [])
    assets_records = checks.records(assets, '/assets', SCENE_MAX_ASSETS, 'too many assets')
    if not isinstance(assets, list):
        assets = []
    asset_ids = set()
    asset_kinds = {'builtin', 'usd', 'mesh', 'generated', 'diffusion', 'trellis', 'sam3d', 'procedural', 'imported', 'native', 'unknown'} | set(ASSETS)
    for index, asset in assets_records:
        with checks.at(f'/assets/{index}'):
            require(isinstance(asset, dict), 'invalid asset record')
            require({'id', 'kind'} <= set(asset), 'asset needs id and kind')
            ident(asset['id']); require(asset['id'] not in asset_ids, 'duplicate asset ID')
            require(asset['kind'] in asset_kinds, f'asset {asset["id"]}: unsupported asset kind {asset["kind"]!r}; use a source category or registered native asset kind')
            if 'uri' in asset: _optional_string(asset['uri'], 'invalid asset URI', 1000)
            if 'size' in asset: dimensions(asset['size'])
            if 'provenance' in asset:
                require(isinstance(asset['provenance'], dict), 'invalid asset provenance')
                require(set(asset['provenance']) <= {'source', 'generator', 'prompt_id', 'license', 'measured'}, 'invalid asset provenance fields')
        if isinstance(asset, dict) and isinstance(asset.get('id'), str):
            asset_ids.add(asset['id'])

    materials = p.get('materials', [])
    materials_records = checks.records(materials, '/materials', SCENE_MAX_ASSETS, 'too many materials')
    if not isinstance(materials, list):
        materials = []
    material_ids = set()
    for index, material in materials_records:
        with checks.at(f'/materials/{index}'):
            require(isinstance(material, dict) and {'id', 'name'} <= set(material), 'material needs id and name')
            ident(material['id']); require(material['id'] not in material_ids, 'duplicate material ID')
            _optional_string(material['name'], 'invalid material name')
            if 'base_color' in material: numbers(material['base_color'], 3, 0, 1)
            if 'appearance' in material:validate_appearance(material['appearance'])
            for key in ('roughness', 'metallic', 'specular', 'ior'):
                if key in material:
                    lo, hi = ((1, 3) if key == 'ior' else (0, 1))
                    require(type(material[key]) in (int, float) and math.isfinite(material[key]) and lo <= material[key] <= hi, 'invalid material parameter')
        if isinstance(material, dict) and isinstance(material.get('id'), str):
            material_ids.add(material['id'])

    lights = p.get('lights', [])
    lights_records = checks.records(lights, '/lights', 256, 'too many lights')
    if not isinstance(lights, list):
        lights = []
    light_ids = set()
    for index, light in lights_records:
        with checks.at(f'/lights/{index}'):
            require(isinstance(light, dict) and {'id', 'kind'} <= set(light), 'light needs id and kind')
            require(set(light) <= LIGHT_FIELDS, 'invalid light fields')
            ident(light['id']); require(light['id'] not in light_ids, 'duplicate light ID')
            _optional_string(light['kind'], 'invalid light kind', 40)
            require(light['kind'] in LIGHT_KINDS, 'unsupported light kind')
            require('position' in light or light['kind'] in {'environment','directional','sun'}, f'light {light["id"]}: local {light["kind"]} light needs position:[x,y,z]')
            if 'position' in light:numbers(light['position'], 3, -1000, 1000)
            if 'direction' in light:
                numbers(light['direction'], 3, -math.inf, math.inf)
                require(sum(value*value for value in light['direction']) >= 1e-12, 'light direction must be nonzero')
            if 'color' in light: numbers(light['color'], 3, 0, 1)
            if 'angle' in light:
                require(light['kind'] in {'sun','directional'} and type(light['angle']) in (int,float) and math.isfinite(light['angle']) and 0 <= light['angle'] <= 180,
                        f'light {light["id"]}: angle is the sun/directional angular diameter in degrees (0..180)')
            if 'size' in light:
                require(isinstance(light['size'],list) and len(light['size'])==2, f'light {light["id"]}: size must be [width,height], exactly two numbers (not a 3D geometry size)')
                numbers(light['size'], 2, .001, 1000)
            for key in ('intensity', 'temperature', 'inner_cone_deg', 'outer_cone_deg'):
                if key in light: require(type(light[key]) in (int, float) and math.isfinite(light[key]) and 0 <= light[key] <= 100000, 'invalid light parameter')
            if 'inner_cone_deg' in light: require(light['inner_cone_deg'] <= 180, 'invalid inner cone')
            if 'outer_cone_deg' in light: require(light['outer_cone_deg'] <= 180, 'invalid outer cone')
            if light['kind'] == 'spot': require(0 <= light.get('inner_cone_deg', 0) <= light.get('outer_cone_deg', 45) <= 180 and light.get('outer_cone_deg', 45) > 0, 'invalid spot cone angles')
            if 'cast_shadows' in light: require(type(light['cast_shadows']) is bool, 'invalid cast_shadows')
            if 'enabled' in light: require(type(light['enabled']) is bool, 'invalid light enabled')
            if 'environment_id' in light:
                require(light['kind']=='environment', 'environment_id only applies to environment lights')
                require(isinstance(light['environment_id'],str) and light['environment_id'] in ENVIRONMENTS,'environment_id must be a registered sky ID')
        if isinstance(light, dict) and isinstance(light.get('id'), str):
            light_ids.add(light['id'])

    render_environment = p.get('render_environment')
    with checks.at('/render_environment'):
        if render_environment is not None:
            require(isinstance(render_environment, dict), 'invalid render environment')
            require(set(render_environment) <= RENDER_ENVIRONMENT_FIELDS, 'invalid render environment fields')
            if 'renderer' in render_environment:
                require(render_environment['renderer'] in RENDERERS, 'unsupported renderer')
            if 'samples_per_pixel' in render_environment:
                spp = render_environment['samples_per_pixel']
                require(type(spp) is int and 16 <= spp < 2**32, 'samples_per_pixel must be a uint32 of at least 16')
            if 'exposure' in render_environment:
                exposure = render_environment['exposure']
                require(type(exposure) in (int, float) and math.isfinite(exposure) and -10 <= exposure <= 10, 'exposure must be -10..10')

    navigation = p.get('navigation')
    with checks.at('/navigation'):
        if navigation is not None:
            require(isinstance(navigation, dict), 'invalid navigation')
            require(set(navigation) <= {'agents', 'waypoints', 'edges', 'clearance_m', 'source'}, 'invalid navigation fields')
            for key in ('waypoints', 'edges', 'agents'):
                if key in navigation: _bounded_list(navigation[key], 4096, 'navigation graph too large')
            if 'clearance_m' in navigation:
                require(type(navigation['clearance_m']) in (int, float) and math.isfinite(navigation['clearance_m']) and 0 <= navigation['clearance_m'] <= 10, 'invalid navigation clearance')

    objects = {obj['id']: obj for obj in object_rows if isinstance(obj, dict) and isinstance(obj.get('id'), str)}
    camera_rows = p.get('cameras', []) if isinstance(p.get('cameras'), list) else []
    camera_ids = {c['id'] for c in camera_rows if isinstance(c, dict) and isinstance(c.get('id'), str)}
    before_actions = len(checks.issues)
    for field in ('affordances', 'interactions'):
        actions = p.get(field, [])
        action_records = checks.records(actions, '/' + field, 16 if field == 'interactions' else 4096, 'too many interaction affordances')
        action_ids = set()
        for index, action in action_records:
            with checks.at(f'/{field}/{index}'):
                require(isinstance(action, dict) and {'id', 'action'} <= set(action), 'affordance needs id and action')
                ident(action['id']); _optional_string(action['action'], 'invalid affordance action', 80)
                require(action['id'] not in action_ids, 'duplicate interaction ID'); action_ids.add(action['id'])
                if 'object_id' in action: require(action['object_id'] in objects, 'affordance object must exist')
                if 'target_id' in action: require(action['target_id'] in objects, 'affordance target must exist')
                if field == 'interactions': require(action['action'] in {'apply_force','robot_push','record_trajectory'}, 'unsupported executable interaction; use affordances for declarations')
                if 'recording' in action:
                    recording = action['recording']
                    require(isinstance(recording, dict) and set(recording) <= {'every_steps', 'spatialtree', 'camera_path'}, 'recording accepts every_steps, spatialtree and camera_path')
                    if 'every_steps' in recording:
                        require(type(recording['every_steps']) is int and recording['every_steps'] > 0, 'recording.every_steps must be a positive integer')
                    if 'spatialtree' in recording:
                        _validate_spatialtree_recording(recording['spatialtree'])
                    if 'camera_path' in recording:
                        _validate_camera_path(recording['camera_path'])
                if action['action'] == 'record_trajectory':
                    require({'id','action','camera_id','duration_steps','recording'} == set(action),
                            'record_trajectory needs id, action, camera_id, duration_steps and recording')
                    require(action['camera_id'] in camera_ids, 'trajectory camera_id must name an existing camera')
                    require(type(action['duration_steps']) is int and 1 <= action['duration_steps'] <= 10000,
                            'record_trajectory.duration_steps must be 1..10000')
                    require('spatialtree' in action['recording'], 'record_trajectory requires recording.spatialtree')
                    require(action['recording'].get('every_steps', 1) <= action['duration_steps'], 'recording interval exceeds trajectory duration')
                    if 'camera_path' in action['recording']:
                        require(action['recording']['camera_path'][-1]['step'] <= action['duration_steps'], 'camera path exceeds trajectory duration')
                if action['action'] == 'robot_push' and (field == 'interactions' or 'interactions' not in p):
                    from capabilities.scene_authoring.contracts.robot_actions import validate_robot_action
                    validate_robot_action(action, objects)
                    ident(action['robot_id'])
                    for waypoint in action['waypoints']: ident(waypoint['id'])
                    if 'camera_id' in action:
                        require(action['camera_id'] in camera_ids, 'interaction camera_id must name an existing camera')
                if action['action'] == 'apply_force' and (field == 'interactions' or 'interactions' not in p):
                    _validate_force_action(action, objects)
                    if 'camera_id' in action:
                        require(action['camera_id'] in camera_ids, 'interaction camera_id must name an existing camera')
    if len(checks.issues) == before_actions:
        with checks.at('/interactions'):
            executable = [action for action in p.get('interactions', p.get('affordances', [])) if action['action'] == 'apply_force']
            require(len(executable) <= 16, 'too many executable interactions')
            require(sum(action.get('duration_steps', 30) + action.get('observe_steps', 90) for action in executable) <= 10000, 'interaction step budget exceeded')
            from capabilities.scene_authoring.contracts.robot_actions import robot_step_count
            robot_actions=[a for a in p.get('interactions',p.get('affordances',[])) if a['action']=='robot_push']
            camera_steps = sum(a['duration_steps'] for a in p.get('interactions',p.get('affordances',[])) if a['action']=='record_trajectory')
            require(camera_steps+sum(robot_step_count(a) for a in robot_actions)+sum(a.get('duration_steps',30)+a.get('observe_steps',90) for a in executable)<=10000,'interaction step budget exceeded')
            robots={}
            for action in robot_actions:
                placement=(action['robot_base_position'],action.get('robot_base_orientation_wxyz',[1.,0.,0.,0.]))
                require(action['robot_id'] not in robots or robots[action['robot_id']]==placement,'shared robot_id must keep its base pose')
                robots[action['robot_id']]=placement

    layers = p.get('semantic_layers', [])
    layers_records = checks.records(layers, '/semantic_layers', 128, 'too many semantic layers')
    if not isinstance(layers, list):
        layers = []
    layer_ids = set()
    for index, layer in layers_records:
        with checks.at(f'/semantic_layers/{index}'):
            require(isinstance(layer, dict) and {'id', 'name'} <= set(layer), 'semantic layer needs id and name')
            ident(layer['id']); require(layer['id'] not in layer_ids, 'duplicate semantic layer ID')
            _optional_string(layer['name'], 'invalid semantic layer name')
            if 'labels' in layer:
                _bounded_list(layer['labels'], 4096, 'too many semantic labels')
                require(all(isinstance(label, str) and label for label in layer['labels']), 'invalid semantic label')
            if 'authority' in layer:
                require(layer['authority'] in {'simulator', 'official', 'imported', 'generated', 'unknown'}, 'invalid semantic authority')
        if isinstance(layer, dict) and isinstance(layer.get('id'), str):
            layer_ids.add(layer['id'])

    graph = p.get('scene_graph')
    with checks.at('/scene_graph'):
        if graph is not None:
            require(isinstance(graph, dict) and set(graph) <= {'nodes', 'edges'}, 'invalid scene graph')
            _bounded_list(graph.get('nodes', []), 2048, 'scene graph too large')
            _bounded_list(graph.get('edges', []), 4096, 'scene graph too large')
            for edge in graph.get('edges', []):
                require(isinstance(edge, dict) and {'source', 'target'} <= set(edge), 'graph edge needs source and target')

    provenance = p.get('provenance')
    with checks.at('/provenance'):
        if 'provenance' in p:
            require(isinstance(provenance, dict), 'invalid scene provenance')
            require(set(provenance) <= {'sources', 'generator', 'created_by', 'revision', 'notes'}, 'invalid scene provenance fields')
            if 'sources' in provenance:
                _bounded_list(provenance['sources'], 512, 'too many provenance sources')
                require(all(isinstance(source, (str, dict)) for source in provenance['sources']), 'invalid provenance source')

    # Cross-references and optional metadata are independent of geometry fields.
    for index, obj in enumerate(object_rows):
        if not isinstance(obj, dict):
            continue
        path = f'/objects/{index}'
        object_id = obj.get('id')
        for field, known, message in (
                ('room_id', room_ids, 'object room must reference a room'),
                ('zone_id', zone_ids, 'object zone must reference a zone'),
                ('material_id', material_ids, 'object material must reference a material')):
            if field in obj:
                checks.require(path + '/' + field, isinstance(obj[field], str) and obj[field] in known,
                               message, object_id=object_id)
        with checks.at(path + '/appearance', object_id=object_id):
            material = next((item for item in materials if isinstance(item, dict) and
                             item.get('id') == obj.get('material_id')), {})
            appearance = obj.get('appearance', {})
            inherited = material.get('appearance', {})
            if isinstance(appearance, dict) and isinstance(inherited, dict) and isinstance(obj.get('kind'), str):
                effective_appearance = {**inherited, **appearance}
                if 'native_opacity_multiplier' in effective_appearance:
                    require(ASSETS.get(obj['kind'], {}).get('supports_native_opacity_multiplier') is True,
                            f'object {object_id}: native_opacity_multiplier requires a registered capable native asset; it cannot replace generic object opacity')
                if 'texture_id' in effective_appearance:
                    require(obj['kind'] in {'box', 'sphere', 'cylinder', 'container', 'composite'},
                            f'object {object_id}: registered texture_id supports primitives, containers and composites; imported meshes retain their source UV materials')
                for field in ('uv_scale_m', 'texture_tint'):
                    if field in effective_appearance:
                        require('texture_id' in effective_appearance,
                                f'object {object_id}: {field} needs a registered texture_id')
            else:
                effective_appearance = {}
        parts = obj.get('parts', [])
        for part_index, part in enumerate(parts if isinstance(parts, list) else []):
            if not isinstance(part, dict):
                continue
            with checks.at(f'{path}/parts/{part_index}/appearance', object_id=object_id):
                if 'material_id' in part:
                    require(isinstance(part['material_id'], str) and part['material_id'] in material_ids,
                            f'object {object_id}.parts[{part_index}]: material_id must reference a scene material')
                    part_material = next(item for item in materials if isinstance(item, dict) and
                                         item.get('id') == part['material_id'])
                    inherited = part_material.get('appearance', {})
                else:
                    inherited = effective_appearance
                appearance = part.get('appearance', {})
                if not isinstance(inherited, dict) or not isinstance(appearance, dict):
                    continue
                part_appearance = {**inherited, **appearance}
                require('native_opacity_multiplier' not in part_appearance,
                        f'object {object_id}.parts[{part_index}]: native_opacity_multiplier applies only to capable native assets')
                for field in ('uv_scale_m', 'texture_tint'):
                    if field in part_appearance:
                        require('texture_id' in part_appearance,
                                f'object {object_id}.parts[{part_index}]: {field} needs a registered texture_id')
        if 'asset_id' in obj and asset_ids:
            checks.require(path + '/asset_id', isinstance(obj['asset_id'], str) and
                           (obj['asset_id'] in asset_ids or obj.get('kind') == 'mesh'),
                           'object asset must reference a registered asset', object_id=object_id)
        if 'asset_quality' in obj:
            with checks.at(path + '/asset_quality', object_id=object_id):
                require(isinstance(obj['asset_quality'], dict), 'invalid asset quality')
                require(set(obj['asset_quality']) <= {'semantic_review', 'identity_match', 'state_match',
                        'completeness', 'issues', 'repair_prompt', '3d_validated', 'reason'},
                        'invalid asset quality fields')
        for field, maximum, message, nonempty in (
                ('semantic_labels', 32, 'invalid object semantic label', True),
                ('affordances', 64, f'object {object_id}: affordances must be a list of strings, '
                 'not action records; structured action records belong to the top-level affordances or interactions', False),
                ('tags', 64, 'invalid object tag', True)):
            if field in obj:
                with checks.at(path + '/' + field, object_id=object_id):
                    _bounded_list(obj[field], maximum, 'too many object ' + field)
                    require(all(isinstance(item, str) and (item or not nonempty)
                                for item in obj[field]), message)

    return p
# 收集对象种类与部件声明的全部不匹配问题，不改写场景提案。
def part_contract_errors(p):
    """Locate all kind/parts mismatches without changing the proposed scene."""
    errors=[]
    if not isinstance(p,dict) or not isinstance(p.get('objects'),list):return errors
    for index,obj in enumerate(p['objects']):
        if not isinstance(obj,dict) or not isinstance(obj.get('parts'),list):continue
        prefix=f'object {obj.get("id","?")} /objects/{index}'
        parts=obj['parts'];kind=obj.get('kind')
        if kind=='composite' and not parts:
            errors.append(f'{prefix}: composite needs parts; supply nonempty parts or replace the entire record with a supported asset kind and parts=[]')
        elif kind!='composite' and parts:
            errors.append(f'{prefix}: only composites accept parts; kind={kind!r} requires parts=[]; use composite for explicit parts or clear parts for the selected asset kind')
        for pi,part in enumerate(parts):
            if isinstance(part,dict) and part.get('shape') not in {'box','sphere','cylinder'}:
                errors.append(f'{prefix}/parts/{pi}/shape: invalid part shape {part.get("shape")!r}; allowed: box, sphere, cylinder')
    return errors
# 逐项验证对象和命名材质中的外观合同，返回所有可读错误。
def appearance_contract_errors(p):
    errors=[]
    for section in ('objects','materials'):
        items=p.get(section,[])
        if not isinstance(items,list):continue
        for index,item in enumerate(items):
            if not isinstance(item,dict) or 'appearance' not in item:continue
            context=f'{section[:-1]} {item.get("id","?")} /{section}/{index}/appearance'
            try:validate_appearance(item['appearance'],context)
            except ValueError as exc:errors.append(f'{context}: {exc}')
    return errors


# 批量校验对象字段、表示方式、尺寸、物理和关联引用的合同。
def _validate_object_fields(obj, index, checks):
    path = f'/objects/{index}'
    if not checks.require(path, isinstance(obj, dict), 'invalid object'):
        return
    object_id = obj.get('id')
    required = {'id', 'label', 'kind', 'size', 'xy', 'support', 'base_z', 'dynamic', 'mass_kg'}
    optional = {'color', 'yaw_deg', 'parts', 'generation', 'asset_id', 'asset_quality', 'native_asset',
                'mesh_transform', 'render_representation', 'cast_shadows', 'physics',
                'appearance', 'room_id', 'zone_id', 'material_id', 'semantic_labels',
                'affordances', 'tags', 'metadata', 'source_ref'}
    checks.require(path, required <= set(obj) <= required | optional,
                   f'object {object_id}: invalid object fields; missing={sorted(required-set(obj))}, '
                   f'unsupported={sorted(set(obj)-required-optional)}. '
                   f'Allowed optional fields={sorted(optional)}; provenance belongs to top-level, '
                   'while object notes belong in metadata/source_ref', object_id=object_id)

    # 仅校验已经声明的对象字段，并为错误绑定对象标识及完整路径。
    def check(field, function, *args):
        if field not in obj:
            return False
        return checks.check(path + '/' + field, function, *args, object_id=object_id)

    check('id', ident, object_id)
    check('label', require, isinstance(obj.get('label'), str) and 0 < len(obj['label']) < 100,
          'invalid semantic label')
    kind = obj.get('kind')
    kind_valid = check('kind', require, isinstance(kind, str) and
                       kind in {'box', 'sphere', 'cylinder', 'container', 'composite',
                                'generated', 'mesh', 'native'} | set(ASSETS), 'unsupported builder')
    if kind_valid and kind == 'generated':
        generation = obj.get('generation')
        checks.require(path + '/generation', isinstance(generation, dict) and
                       isinstance(generation.get('prompt'), str),
                       'generated asset needs a visual generation prompt', object_id=object_id)
    if kind_valid and kind == 'mesh':
        checks.check(path + '/asset_id', ident, obj.get('asset_id'), object_id=object_id)
    if kind_valid and kind == 'native':
        checks.check(path + '/native_asset', validate_native_reference, obj.get('native_asset'),
                     object_id=object_id)
        checks.require(path, not {'asset_id', 'generation', 'asset_quality'} & set(obj),
                       'native objects use native_asset exclusively; do not mix mesh/generation inputs',
                       object_id=object_id)
    elif 'native_asset' in obj:
        checks.require(path + '/native_asset', False, 'native_asset requires kind=native',
                       object_id=object_id)
    check('render_representation', require, obj.get('render_representation') in
          ('auto', 'gaussian', 'mesh'), 'render_representation must be auto, gaussian or mesh')
    check('cast_shadows', require, type(obj.get('cast_shadows')) is bool,
          'object cast_shadows must be boolean')
    if 'mesh_transform' in obj:
        with checks.at(path + '/mesh_transform', object_id=object_id):
            require(kind in ('mesh', 'generated'),
                    'mesh_transform only applies to imported or generated meshes')
            transform = obj['mesh_transform']
            require(isinstance(transform, dict) and
                    set(transform) <= {'orientation_deg_xyz', 'scale_mode'},
                    'invalid mesh_transform fields')
            if 'orientation_deg_xyz' in transform:
                numbers(transform['orientation_deg_xyz'], 3, -360, 360)
            require(transform.get('scale_mode', 'uniform_fit') == 'uniform_fit',
                    f'object {object_id}: mesh_transform.scale_mode must be "uniform_fit", '
                    f'got {transform.get("scale_mode")!r}; preserve proportions and change '
                    'orientation_deg_xyz for pose, not scale_mode')
    with checks.at(path + '/physics', object_id=object_id):
        physics = obj.get('physics', {})
        require(isinstance(physics, dict), 'invalid physics properties')
        require(set(physics) <= {'static_friction', 'dynamic_friction', 'restitution',
                                'mass_source', 'collider'}, 'invalid physics properties')
        for key, maximum in (('static_friction', 3), ('dynamic_friction', 3), ('restitution', 1)):
            if key in physics:
                checks.check(path + '/physics/' + key, numbers, [physics[key]], 1, 0,
                             maximum, object_id=object_id)
        if 'collider' in physics:
            require(physics['collider'] in ('convexHull', 'convexDecomposition'),
                    'unsupported collider')
    appearance_valid = checks.check(path + '/appearance', validate_appearance,
                                     obj.get('appearance', {}), object_id=object_id)
    if appearance_valid and kind_valid and 'native_opacity_multiplier' in obj.get('appearance', {}):
        checks.require(path + '/appearance/native_opacity_multiplier',
                       ASSETS.get(kind, {}).get('supports_native_opacity_multiplier') is True,
                       f'object {object_id}: native_opacity_multiplier requires a registered capable native asset',
                       object_id=object_id)
    context = f'object {object_id}'
    check('size', dimensions, obj.get('size'), context + '.size')
    check('color', numbers, obj.get('color'), 3, 0, 1, context + '.color')
    check('xy', numbers, obj.get('xy'), 2, -1000, 1000, context + '.xy')
    for field in ('base_z', 'yaw_deg'):
        check(field, numbers, [obj.get(field)], 1, -1000, 1000, context + '.' + field)
    dynamic_valid = check('dynamic', require, type(obj.get('dynamic')) is bool,
                          context + ': dynamic must be boolean')
    if dynamic_valid:
        mass = obj.get('mass_kg')
        check('mass_kg', require, type(mass) in (float, int) and math.isfinite(mass) and
              (.001 if obj['dynamic'] else 0) <= mass <= 1000000,
              context + ': invalid mass_kg; dynamic bodies require mass >= 0.001, '
              'static bodies allow unused mass=0')
    check('support', require, isinstance(obj.get('support'), str),
          context + ': support must be ground or an object ID')
    check('metadata', require, isinstance(obj.get('metadata'), dict),
          context + ': metadata must be an object')
    parts = obj.get('parts', [])
    if not checks.require(path + '/parts', isinstance(parts, list), 'invalid parts',
                          object_id=object_id):
        return
    if kind_valid:
        checks.require(path + '/parts', bool(parts) if kind == 'composite' else not parts,
                       'composite needs parts' if kind == 'composite' else 'only composites accept parts',
                       object_id=object_id)
    for part_index, part in enumerate(parts):
        part_path = f'{path}/parts/{part_index}'
        with checks.at(part_path, object_id=object_id):
            require(isinstance(part, dict), 'invalid part fields')
            require({'shape', 'size', 'offset', 'color'} <= set(part) <=
                    {'shape', 'size', 'offset', 'color', 'rotation_deg_xyz', 'material_id', 'appearance'},
                    'invalid part fields')
            checks.require(part_path + '/shape', part['shape'] in ('box', 'sphere', 'cylinder'),
                           'invalid part shape; allowed: box, sphere, cylinder', object_id=object_id)
            checks.check(part_path + '/appearance', validate_appearance,
                         part.get('appearance', {}), object_id=object_id)
            if 'material_id' in part:
                checks.check(part_path + '/material_id', ident, part['material_id'], object_id=object_id)
            part_context = f'{context}.parts[{part_index}]'
            checks.check(part_path + '/size', dimensions, part['size'],
                         part_context + '.size', object_id=object_id)
            for field, length, low, high in (('offset', 3, -1000, 1000), ('color', 3, 0, 1),
                                             ('rotation_deg_xyz', 3, -360, 360)):
                if field in part:
                    checks.check(part_path + '/' + field, numbers, part[field], length, low, high,
                                 part_context + '.' + field, object_id=object_id)


# 独立校验相机字段、位姿、光学参数和位置目标不重合条件。
def _validate_camera_fields(camera, index, checks):
    path = f'/cameras/{index}'
    if not checks.require(path, isinstance(camera, dict), f'camera {index}: must be an object'):
        return
    fields = {'position', 'target', 'id', 'up', 'focal_length_mm', 'horizontal_aperture_mm',
              'role', 'allow_external'}
    checks.require(path, {'position', 'target'} <= set(camera) <= fields,
                   f'camera {index}: fields are position,target and optional '
                   'id,up,focal_length_mm,horizontal_aperture_mm,role,allow_external')
    if 'id' in camera:
        checks.check(path + '/id', ident, camera['id'])
    position_ok = checks.check(path + '/position', numbers, camera.get('position'), 3,
                               -1000, 1000, f'camera {camera.get("id", index)}.position')
    target_ok = checks.check(path + '/target', numbers, camera.get('target'), 3,
                             -1000, 1000, f'camera {camera.get("id", index)}.target')
    if position_ok and target_ok:
        checks.require(path, sum((a-b)**2 for a, b in zip(camera['position'], camera['target'])) > 1e-8,
                       f'degenerate camera {index}: position and target must be more than '
                       '0.0001 m apart; vertical views are allowed')
    for field in ('focal_length_mm', 'horizontal_aperture_mm'):
        if field in camera:
            value = camera[field]
            checks.require(path + '/' + field, type(value) in (int, float) and
                           math.isfinite(value) and value > 0,
                           f'camera {index}.{field}: expected a positive finite number')
    if 'up' in camera:
        up_ok = checks.check(path + '/up', numbers, camera['up'], 3, -math.inf, math.inf,
                              f'camera {index}.up')
        if up_ok and position_ok and target_ok:
            direction = [camera['target'][i]-camera['position'][i] for i in range(3)]
            up = camera['up']
            cross = [direction[1]*up[2]-direction[2]*up[1],
                     direction[2]*up[0]-direction[0]*up[2],
                     direction[0]*up[1]-direction[1]*up[0]]
            checks.require(path + '/up', any(v != 0 for v in cross),
                           f'camera {index}.up: cannot be zero or parallel to the view direction')
    if 'role' in camera:
        checks.require(path + '/role', isinstance(camera['role'], str) and bool(camera['role']),
                       f'camera {index}.role: expected a nonempty string')
    if 'allow_external' in camera:
        checks.require(path + '/allow_external', type(camera['allow_external']) is bool,
                       f'camera {index}.allow_external: expected a boolean')


# 汇总场景中各独立字段和记录的合同错误，不自动修补非法提案。
def program_contract_issues(program):
    """Batch all independent fields/records; never patch a malformed proposal."""
    from capabilities.scene_authoring.validation.contract_diagnostics import ContractDiagnostics
    checks = ContractDiagnostics()
    if not checks.require('', isinstance(program, dict), 'scene must be an object'):
        return checks.issues
    checks.require('/schema', program.get('schema') in SCENE_SCHEMAS,
                   'unsupported scene schema')
    required = {'schema', 'scene_id', 'title', 'objects', 'cameras', 'assumptions'}
    allowed = required | {'rooms', 'zones', 'portals', 'assets', 'materials', 'lights',
                          'navigation', 'affordances', 'interactions', 'semantic_layers',
                          'scene_graph', 'provenance', 'render_environment'}
    checks.require('', required <= set(program) <= allowed,
                   f'invalid scene fields; missing={sorted(required-set(program))}, '
                   f'unsupported={sorted(set(program)-allowed)}')
    checks.check('/scene_id', ident, program.get('scene_id'))
    checks.require('/title', isinstance(program.get('title'), str) and len(program['title']) < 200,
                   'invalid title')
    ids = set()
    objects = program.get('objects')
    for index, obj in checks.records(objects, '/objects', SCENE_V2_MAX_OBJECTS,
                                     'entity count exceeds execution budget', minimum=1):
        _validate_object_fields(obj, index, checks)
        if isinstance(obj, dict) and isinstance(obj.get('id'), str):
            checks.require(f'/objects/{index}/id', obj['id'] not in ids, 'duplicate ID',
                           object_id=obj['id'])
            ids.add(obj['id'])
    if isinstance(objects, list):
        from capabilities.scene_authoring.geometry.support_graph import support_reference_issues
        checks.issues.extend(support_reference_issues(objects))
    camera_ids = set()
    for index, camera in checks.records(program.get('cameras'), '/cameras', 64,
                                        'camera count exceeds execution budget', minimum=1):
        _validate_camera_fields(camera, index, checks)
        if isinstance(camera, dict) and isinstance(camera.get('id'), str):
            checks.require(f'/cameras/{index}/id', camera['id'] not in camera_ids,
                           f'camera {index}: duplicate id')
            camera_ids.add(camera['id'])
    with checks.at('/assumptions'):
        assumptions = program.get('assumptions')
        require(isinstance(assumptions, list) and len(assumptions) <= 30, 'invalid assumptions')
        require(all(isinstance(item, str) for item in assumptions), 'assumptions must be strings')
    _validate_scene_extensions(program, checks)
    return checks.issues


# 执行完整场景合同校验，有问题时抛出结构化异常，否则返回原对象。
def validate_program(p):
    from capabilities.scene_authoring.validation.contract_diagnostics import ProgramValidationError
    issues = program_contract_issues(p)
    if issues:
        raise ProgramValidationError(issues)
    return p
