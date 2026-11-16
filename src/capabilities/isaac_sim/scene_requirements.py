"""Admission checks for the SceneProgram adapter, separate from native RPC tools."""
from pathlib import Path
import re

from capabilities.scene_authoring.catalog.native import ASSETS


UNSUPPORTED_MATERIAL_FIELDS = (
    'texture_id', 'uv_scale_m', 'texture_tint', 'native_opacity_multiplier', 'specular', 'ior',
)


class UnsupportedScene(ValueError):
    """An explicit requirement has no faithful SceneProgram adapter mapping."""
    code = 'unsupported_scene'

    def __init__(self, message, *, issues=()):
        self.issues = list(issues)
        super().__init__(message)


def validate_scene_requirements(program):
    _require_supported(scene_requirement_issues(program))


def _records(value):
    return value if isinstance(value, list) else []


def _issue(path, message, object_id=None):
    return {'code': 'unsupported_capture_requirement', 'path': path, 'message': message,
            'basis': 'isaac_sim_capture_adapter', 'severity': 'error', 'blocking': True,
            **({'object_ids': [object_id]} if isinstance(object_id, str) else {})}


def scene_requirement_issues(program):
    """Actions have no implicit SceneProgram-to-native command mapping."""
    if not isinstance(program, dict):
        return []
    field = 'interactions' if 'interactions' in program else 'affordances'
    return [_issue(f'/{field}/{index}',
                   'This SceneProgram action requires native session commands and world.collect receipts. '
                   'Use benchforge_isaac_sim with the live catalog before submitting a capture; '
                   'robot/contact/trajectory execution cannot be silently substituted.',
                   row.get('object_id'))
            for index, row in enumerate(_records(program.get(field, [])))
            if isinstance(row, dict) and row.get('action') in ('apply_force', 'robot_push', 'record_trajectory')]


def capture_requirement_issues(program, assets_root=None, settings=None):
    """Collect independent adapter limitations without compiling or contacting a GPU.

    Malformed fields remain the scene contract's responsibility. This reports
    explicit unsupported requirements even when unrelated contract fields fail.
    Asset contents and current native capabilities still need capture validation.
    """
    issues = scene_requirement_issues(program)
    if not isinstance(program, dict):
        return issues
    named = {row['id']: (index, row) for index, row in enumerate(_records(program.get('materials')))
             if isinstance(row, dict) and isinstance(row.get('id'), str)}
    checked_materials = set()

    def material_fields(row, path, object_id):
        appearance = row.get('appearance')
        appearance = appearance if isinstance(appearance, dict) else {}
        for key in UNSUPPORTED_MATERIAL_FIELDS:
            location = path + ('/appearance/' if key in appearance else '/') + key
            if (key in row or key in appearance) and location not in checked_materials:
                checked_materials.add(location)
                issues.append(_issue(location, f'A100 SceneProgram material {key} has no faithful mapping; '
                                     'inspect the live catalog', object_id))

    def used_material(row, path, object_id):
        material_fields(row, path, object_id)
        identity = row.get('material_id')
        if isinstance(identity, str) and identity in named:
            index, material = named[identity]
            material_fields(material, f'/materials/{index}', object_id)

    for index, obj in enumerate(_records(program.get('objects'))):
        if not isinstance(obj, dict):
            continue
        path, identity = f'/objects/{index}', obj.get('id')
        asset_id, kind = obj.get('asset_id'), obj.get('kind')
        if not isinstance(kind, str):
            continue
        if kind == 'mesh' and isinstance(asset_id, str) and asset_id in ASSETS:
            kind = asset_id
        if obj.get('render_representation') == 'gaussian':
            issues.append(_issue(path + '/render_representation',
                'SceneProgram capture requires mesh primary-surface annotations; Gaussian uses the native render API', identity))
        elif (assets_root is not None and isinstance(asset_id, str)
              and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', asset_id)
              and obj.get('render_representation', 'auto') != 'mesh'
              and (Path(assets_root) / asset_id / 'gaussian.npz').is_file()):
            issues.append(_issue(path + '/render_representation',
                'A paired Gaussian asset requires an explicit mesh representation for primary-surface scene capture; '
                'appearance is not silently substituted', identity))
        if obj.get('cast_shadows') is False:
            issues.append(_issue(path + '/cast_shadows',
                                 'A100 scene capture cannot disable individual shadow casting', identity))
        if kind in ASSETS:
            if settings is not None and not settings.get('asset_sources', {}).get(kind):
                issues.append(_issue(path + '/kind', f'Native asset {kind} needs an explicit '
                    'providers.IsaacSim4A100.asset_sources binding; no proxy substitution', identity))
            continue
        used_material(obj, path, identity)
        if kind == 'composite':
            for part_index, part in enumerate(_records(obj.get('parts'))):
                if isinstance(part, dict):
                    used_material(part, f'{path}/parts/{part_index}', identity)
    environment = program.get('render_environment')
    if isinstance(environment, dict):
        if environment.get('renderer') == 'RaytracedLighting':
            issues.append(_issue('/render_environment/renderer',
                'A100 does not provide the RTX RaytracedLighting renderer; select PathTracing explicitly'))
        if isinstance(environment.get('exposure'), (int, float)) and environment['exposure'] != 0:
            issues.append(_issue('/render_environment/exposure',
                'A100 scene.capture has no exposure control; do not silently discard exposure'))
    spp = (environment if isinstance(environment, dict) else {}).get(
        'samples_per_pixel', (settings or {}).get('samples_per_pixel', 32))
    if isinstance(spp, (int, float)) and not 1 <= spp <= 256:
        issues.append(_issue('/render_environment/samples_per_pixel',
            'The running A100 scene.capture contract accepts samples_per_pixel up to 256'))
    for index, light in enumerate(_records(program.get('lights'))):
        if not isinstance(light, dict) or light.get('enabled') is False:
            continue
        path = f'/lights/{index}'
        if light.get('kind') not in ('environment', 'area'):
            issues.append(_issue(path + '/kind', 'A100 scene.capture supports constant environment and rectangular '
                                 'area lights; inspect native catalog for other workflows'))
        for key in ('environment_id', 'temperature', 'cast_shadows'):
            if key in light:
                issues.append(_issue(path + '/' + key,
                                     f'A100 scene.capture has no light {key} mapping; inspect the live catalog'))
    return issues


def _require_supported(issues):
    if issues:
        raise UnsupportedScene('\n'.join(row['path'] + ': ' + row['message'] for row in issues),
                               issues=issues)


def validate_capture_requirements(program, assets_root, settings):
    _require_supported(capture_requirement_issues(program, assets_root, settings))
