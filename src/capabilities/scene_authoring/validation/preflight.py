"""Batch static authoring checks; captured native/visual evidence stays separate."""
from __future__ import annotations

from typing import Any
import json
from pathlib import Path
import re

from capabilities.scene_authoring.catalog.native import ASSETS, normalize_native_asset_references
from capabilities.scene_authoring.catalog.native_reference import native_size_z_up
from capabilities.scene_authoring.contracts.scene_program import SCENE_V2_MAX_OBJECTS
from capabilities.scene_authoring.validation.program import program_contract_issues
from capabilities.scene_authoring.geometry.preview import preview_scene_geometry, initial_bounds_overlaps
from capabilities.scene_authoring.contracts.scene_design import geometry_exception, scene_brief, saved_design
from capabilities.scene_authoring.validation.diagnostics import SceneDiagnostics, ScenePreflightError
from capabilities.scene_authoring.validation.policy import attachment, check_references, detailed_construction, policy_for, structure


def _inside(point: list[float], center: list[float], size: list[float]) -> bool:
    return all(abs(point[i] - center[i]) <= size[i] / 2 for i in range(3))


def preflight_scene(program: dict[str, Any], *, vertical_tolerance_m: float = 0.05) -> dict[str, Any]:
    rooms = [room for room in program.get("rooms", []) if isinstance(room, dict)]
    records = []
    errors = []
    for index, camera in enumerate(program.get("cameras", [])):
        position = camera.get("position")
        target = camera.get("target")
        record = {"camera_index": index, "camera_id": camera.get("id", index),
                  "position": position, "target": target, "status": "not_applicable"}
        if camera.get("allow_external") or camera.get("role") == "external":
            record.update(status="passed", reason="external_camera_declared")
            records.append(record)
            continue
        containing = []
        for room in rooms:
            bounds = room.get("bounds")
            if isinstance(bounds, dict) and _inside(target, bounds["center"], bounds["size"]):
                containing.append(room)
        if not containing:
            record.update(status="not_applicable", reason="target_not_inside_declared_room")
            records.append(record)
            continue
        floor = max(room.get("floor_z", room["bounds"]["center"][2] - room["bounds"]["size"][2] / 2) for room in containing)
        ceiling = min(room["bounds"]["center"][2] + room["bounds"]["size"][2] / 2 for room in containing)
        record["room_ids"] = [room["id"] for room in containing]
        record["declared_vertical_range_m"] = [floor, ceiling]
        if position[2] > ceiling + vertical_tolerance_m:
            record.update(status="failed", reason="camera_above_declared_ceiling")
            errors.append({"camera_index": index, "camera_id": record["camera_id"],
                           "code": "camera_above_declared_ceiling",
                           "message": "camera is above the ceiling while targeting a declared room",
                           "vertical_range_m": [floor, ceiling], "position_z_m": position[2]})
        elif position[2] < floor - vertical_tolerance_m:
            record.update(status="failed", reason="camera_below_declared_floor")
            errors.append({"camera_index": index, "camera_id": record["camera_id"],
                           "code": "camera_below_declared_floor",
                           "message": "camera is below the floor while targeting a declared room",
                           "vertical_range_m": [floor, ceiling], "position_z_m": position[2]})
        else:
            record.update(status="passed", reason="camera_vertical_position_is_plausible")
        records.append(record)
    return {"schema": "benchforge.scene-preflight/v1", "passed": not errors, "blocking": False,
            "errors": errors, "cameras": records,
            "limits": ["declared room bounds only", "does not infer walls or occlusion", "external cameras are allowed"]}


def _records(program, field):
    value = program.get(field, [])
    return value if isinstance(value, list) else []


def _invalid_at(issues, path):
    return any(row['path'] == path or row['path'].startswith(path + '/') for row in issues)


def _independent_geometry(program, contract_issues, diagnostics):
    """Select safe components for diagnostics only; the submitted input is intact."""
    selected = {}
    indexes = {}
    rows = _records(program, 'objects')
    if len(rows) > SCENE_V2_MAX_OBJECTS:
        diagnostics.skip('object_geometry', '/objects', 'object count exceeds the execution budget')
        return [], {}
    counts = {}
    for obj in rows:
        if isinstance(obj, dict) and isinstance(obj.get('id'), str):
            counts[obj['id']] = counts.get(obj['id'], 0) + 1
    cyclic = {key for issue in contract_issues if issue['code'] == 'cyclic_support'
              for key in issue['object_ids']}
    geometry_fields = {'id', 'kind', 'size', 'xy', 'support', 'base_z', 'yaw_deg', 'dynamic',
                       'parts', 'mesh_transform', 'asset_id', 'native_asset'}
    for index, obj in enumerate(rows):
        path = f'/objects/{index}'
        invalid_geometry = any(row['path'] == path or any(
            row['path'] == path + '/' + field or row['path'].startswith(path + '/' + field + '/')
            for field in geometry_fields) for row in contract_issues)
        if (invalid_geometry or not isinstance(obj, dict) or
                counts.get(obj.get('id')) != 1 or obj.get('id') in cyclic):
            diagnostics.skip('object_geometry', path, 'object contract or support graph is invalid')
            continue
        ok, normalized = diagnostics.attempt('native_asset_alias', path,
            lambda: normalize_native_asset_references({'objects': [obj]})['objects'][0],
            object_ids=[obj['id']], basis='registered_asset_contract')
        if ok:
            selected[obj['id']] = normalized
            indexes[obj['id']] = index
    # Descendants of an invalid support cannot acquire a fabricated height.
    while True:
        removed = [key for key, obj in selected.items()
                   if obj['support'] != 'ground' and obj['support'] not in selected]
        if not removed:
            break
        for key in removed:
            diagnostics.skip('object_geometry', f'/objects/{indexes[key]}',
                             'support component is invalid or unavailable')
            del selected[key]
    return list(selected.values()), indexes


def _design_diagnostics(root, program, policy, diagnostics):
    path = '/provenance/sources'
    ok, saved = diagnostics.attempt('scene_plan', path, lambda: saved_design(program, policy),
                                     basis='saved_scene_plan')
    if not ok:
        diagnostics.skip('geometry_exception', path, 'saved scene plan is invalid')
        diagnostics.skip('scene_brief', path, 'saved scene plan is invalid')
        return None, None, False
    declared, declaration = diagnostics.attempt('geometry_exception', path,
        lambda: geometry_exception(program, policy, resolved_design=saved), basis='saved_scene_plan')
    _, brief = diagnostics.attempt('scene_brief', path,
        lambda: scene_brief(program, policy, root, resolved_design=saved), basis='saved_scene_plan')
    return declaration, brief, declared


def _object_admission(program, objects, indexes, policy, declaration, brief, declared, diagnostics):
    by_id = {obj['id']: obj for obj in objects}
    simplified = set(declaration['object_ids']) if declaration else set()
    mounts = {}
    declared_ids = {row['id'] for row in _records(program, 'objects')
                    if isinstance(row, dict) and isinstance(row.get('id'), str)}
    if policy and not any(not structure(obj) for obj in objects) and not diagnostics.skipped:
        diagnostics.add('foreground_missing', '/objects',
                        'Realistic scenes need recognizable foreground assets', basis='scene_policy')
    for obj in objects:
        key = obj['id']
        path = f'/objects/{indexes[key]}'
        metadata = obj.get('metadata', {})
        raw_mount = metadata.get('spatial_attachment') if isinstance(metadata, dict) else None
        target = raw_mount.get('target') if isinstance(raw_mount, dict) else None
        if not isinstance(metadata, dict) or (isinstance(target, str) and target in declared_ids and target not in by_id):
            ok, mount = False, None
            diagnostics.skip('static_mount', path + '/metadata/spatial_attachment',
                             'mount metadata or target geometry is invalid')
        else:
            ok, mount = diagnostics.attempt('static_mount', path + '/metadata/spatial_attachment',
                                            lambda: attachment(obj, by_id), object_ids=[key])
        if ok:
            mounts[key] = mount
        if not policy or structure(obj):
            continue
        kind = obj['kind']
        if declared and key not in simplified:
            if (kind not in ASSETS and kind not in {'mesh', 'bin', 'container', 'generated', 'native'}
                    and not detailed_construction(obj)):
                diagnostics.add('recognizable_asset_required', path + '/kind',
                    'Default scene design uses recognizable assets; simplified geometry needs an '
                    'explicit scoped declaration in the saved scene plan',
                    basis='scene_policy', object_ids=[key])
            if isinstance(obj.get('label'), str) and re.search(r'cube|立方体', key + ' ' + obj['label'], re.I):
                diagnostics.add('geometry_exception_required', path + '/label',
                    'Default scene design needs an explicit planned geometry exception for cube objects',
                    basis='scene_policy', object_ids=[key])
        if kind in ASSETS or kind == 'native':
            source_size = native_size_z_up(obj['native_asset']) if kind == 'native' else ASSETS[kind]['size']
            ratios = [obj['size'][i] / source_size[i] for i in range(3)]
            if min(ratios) < .5 or max(ratios) > 2 or max(ratios) / min(ratios) > 1.5:
                diagnostics.add('native_asset_scale', path + '/size',
                    'Scale or proportions depart from the declared native source dimensions',
                    basis='declared_native_dimensions' if kind == 'native' else 'registered_asset_dimensions',
                    object_ids=[key], ratios=ratios)
        if ok and not mount and obj['support'] == 'ground' and abs(obj['base_z']) > .03:
            diagnostics.add('ground_offset', path + '/base_z',
                'Declared initial geometry floats above or penetrates ground; model actual support/attachment. '
                'This is an initial placement check, not an observed contact or settling result.',
                basis='declared_support', object_ids=[key], base_z_m=obj['base_z'])
        if obj['dynamic'] and isinstance(obj.get('physics', {}), dict):
            required = {'static_friction', 'dynamic_friction', 'restitution', 'mass_source'}
            missing = sorted(required - set(obj.get('physics', {})))
            if missing:
                diagnostics.add('physical_material_missing', path + '/physics',
                    'Dynamic object needs explicit friction, restitution and mass provenance',
                    basis='scene_policy', object_ids=[key], missing_fields=missing)
    if brief:
        for role in brief['object_roles']:
            if role.get('representation') in {'native_asset', 'articulated_robot'}:
                obj = by_id.get(role['object_id'])
                if obj is None:
                    diagnostics.skip('required_native_asset', '/scene_brief/object_roles',
                                     'object contract or support component is invalid')
                elif obj['kind'] not in ASSETS and obj['kind'] != 'native':
                    diagnostics.add('required_native_asset', f'/objects/{indexes[obj["id"]]}/kind',
                        'Requires a registered native asset or an authorized uploaded native handle; primitive geometry and metadata '
                        'cannot substitute for asset identity', basis='saved_scene_plan',
                        object_ids=[obj['id']])
        for index, relation in enumerate(brief['relations']):
            obj = by_id.get(relation['source'])
            if obj is None:
                diagnostics.skip('brief_relation', f'/scene_brief/relations/{index}',
                                 'source object contract or support component is invalid')
                continue
            expected = relation['target']
            actual = obj['support'] if relation['type'] == 'supported_by' else (
                mounts.get(obj['id']) or {}).get('target') if relation['type'] == 'mounted_to' else expected
            if actual != expected:
                diagnostics.add('scene_brief_relation_mismatch', f'/objects/{indexes[obj["id"]]}',
                    obj['id'] + ': support or mounting contradicts saved scene brief',
                    basis='saved_scene_plan', object_ids=[obj['id']] + ([expected] if expected in by_id else []),
                    relation_index=index, expected_target=expected, declared_target=actual)
    return mounts, simplified


def _geometry_diagnostics(root, program, objects, indexes, mounts, simplified,
                          policy, allow_generated, diagnostics):
    cache = {}
    assets_root = Path(root) / 'generated_assets'
    resolved_assets_root = assets_root.resolve()
    simplified_assets = {obj.get('asset_id') for obj in objects if obj['id'] in simplified}

    def mesh(asset_id):
        if asset_id not in cache:
            path = assets_root / asset_id / 'mesh.json'
            resolved_path = path.resolve()
            if not resolved_path.is_relative_to(resolved_assets_root):
                raise ValueError('asset path escapes registry')
            try:
                value = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(value, dict):
                    raise ValueError('mesh record must be an object')
                if policy and len(value.get('vertices', [])) <= 8 and asset_id not in simplified_assets:
                    raise ValueError('Mesh is a primitive proxy, not a recognizable scene asset: ' + asset_id)
                cache[asset_id] = value
            except (ValueError, OSError, TypeError) as error:
                cache[asset_id] = error
        if isinstance(cache[asset_id], Exception):
            raise cache[asset_id]
        return cache[asset_id]

    # Asset identity/availability does not depend on a valid size, pose or camera.
    selected_ids = {obj['id'] for obj in objects}
    for index, obj in enumerate(_records(program, 'objects')[:SCENE_V2_MAX_OBJECTS]):
        if (not isinstance(obj, dict) or obj.get('kind') != 'mesh' or
                isinstance(obj.get('id'), str) and obj['id'] in selected_ids):
            continue
        asset_id = obj.get('asset_id')
        if isinstance(asset_id, str) and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', asset_id) and asset_id not in ASSETS:
            diagnostics.attempt('source_geometry_unavailable', f'/objects/{index}/asset_id',
                                lambda: mesh(asset_id), object_ids=[obj['id']] if isinstance(obj.get('id'), str) else [],
                                basis='asset_registry')
    preview = preview_scene_geometry({'scene_id': program.get('scene_id'), 'objects': objects},
                                     mesh_loader=mesh)
    by_id = {obj['id']: obj for obj in objects}
    for row in preview['objects']:
        key = row['object_id']
        row['path'] = f'/objects/{indexes[key]}'
        obj = by_id[key]
        if row.get('unavailable'):
            generated = obj['kind'] == 'generated' and allow_generated
            diagnostics.add('asset_not_realized' if generated else 'source_geometry_unavailable',
                row['path'] + '/asset_id', ('Generated asset is pending: ' if generated else
                'registered mesh unavailable: ') + row['unavailable'], basis='asset_registry',
                severity='pending' if generated else 'error', object_ids=[key], asset_id=obj.get('asset_id'))
        if row['initial_bottom_z_m'] is None:
            diagnostics.skip('initial_placement', row['path'], 'support geometry is unavailable')
        faces = row.get('support_face_options', [])
        if not structure(obj) and not mounts.get(key) and faces and not any(
                face['footprint_within_face'] and abs(face['geometry_bottom_gap_m']) <= .03 for face in faces):
            diagnostics.add('support_contact_unverified', row['path'] + '/support',
                'Initial bounds do not match a full horizontal support face. Inspect the reported '
                'gaps and intended contact; conservative footprint mismatch is not proof of missing contact.',
                basis='initial_source_geometry', severity='warning',
                object_ids=[key, obj['support']], face_options=faces)
    for warning in preview['warnings']:
        if warning['code'] in {'preview_incomplete', 'no_full_box_face_at_declared_height'}:
            continue
        diagnostics.add(warning['code'], f'/objects/{indexes[warning["object_id"]]}', warning['detail'],
                        basis='initial_source_geometry', severity='warning', object_ids=[warning['object_id']])
    for overlap in initial_bounds_overlaps(preview):
        left, right = overlap['object_ids']
        if structure(by_id[left]) and structure(by_id[right]):
            continue
        if (mounts.get(left) or {}).get('target') == right or (mounts.get(right) or {}).get('target') == left:
            continue
        diagnostics.add('initial_bounds_overlap', f'/objects/{indexes[left]}',
            'Conservative initial bounds overlap. Check this pair together; holes, open containers '
            'and intended contact can explain the overlap. Exact mesh intersection is unresolved.',
            basis=overlap['basis'], severity='warning', object_ids=[left, right],
            overlap_extent_m=overlap['overlap_extent_m'])
    return preview


def inspect_program(root, program, *, policy=None, execution_mode=None, allow_generated=True,
                    additional_issues=()):
    """Inspect every safe independent component without capture, generation or edits."""
    diagnostics = SceneDiagnostics()
    contract_issues = program_contract_issues(program)
    diagnostics.issues.extend(contract_issues)
    diagnostics.issues.extend(additional_issues)
    if not isinstance(program, dict):
        diagnostics.skip('scene_checks', '', 'scene must be an object')
        return diagnostics.finish(program, enforced=False)
    if policy is None:
        ok, policy = diagnostics.attempt('scene_policy', '', lambda: policy_for(root, execution_mode),
                                         basis='operator_policy')
        if not ok:
            policy = {}
    declaration, brief, declared = None, None, True
    if policy:
        diagnostics.attempt('excluded_source', '', lambda: check_references(program, policy),
                            basis='operator_policy')
        if not _invalid_at(contract_issues, '/provenance'):
            # Only declared IDs are needed to validate plan references. No fabricated object is added.
            named = [obj for obj in _records(program, 'objects')
                     if isinstance(obj, dict) and isinstance(obj.get('id'), str)]
            declaration, brief, declared = _design_diagnostics(
                root, {**program, 'objects': named}, policy, diagnostics)
        else:
            declared = False
            diagnostics.skip('scene_plan', '/provenance', 'provenance contract is invalid')
    objects, indexes = _independent_geometry(program, contract_issues, diagnostics)
    mounts, simplified = _object_admission(program, objects, indexes, policy, declaration,
                                            brief, declared, diagnostics)
    geometry = _geometry_diagnostics(root, program, objects, indexes, mounts, simplified,
                                      policy, allow_generated, diagnostics)
    rooms = []
    for index, room in enumerate(_records(program, 'rooms')):
        if _invalid_at(contract_issues, f'/rooms/{index}'):
            diagnostics.skip('camera_room_bounds', f'/rooms/{index}', 'room contract is invalid')
        else:
            rooms.append(room)
    camera_checks = []
    for index, camera in enumerate(_records(program, 'cameras')):
        path = f'/cameras/{index}'
        if _invalid_at(contract_issues, path):
            diagnostics.skip('camera_room_bounds', path, 'camera contract is invalid')
            continue
        check = preflight_scene({'rooms': rooms, 'cameras': [camera]})
        camera_checks.extend({**row, 'camera_index': index} for row in check['cameras'])
        for warning in check['errors']:
            diagnostics.add(warning['code'], path + '/position',
                warning['message'] + '; declared room bounds do not establish physical walls or occlusion',
                basis='declared_room_bounds', severity='warning',
                camera_id=camera.get('id', index), position_z_m=warning['position_z_m'],
                vertical_range_m=warning['vertical_range_m'])
    return diagnostics.finish(program, enforced=bool(policy),
        scene_id=program.get('scene_id'),
        scene_style='simplified_geometry' if declaration else 'realistic_assets' if policy else 'unconstrained',
        scene_style_basis='saved declaration or production policy; not verified appearance',
        geometry_exception=declaration, scene_brief=brief, geometry_preview=geometry,
        cameras=camera_checks,
        scope='contracts, declared plans, source assets and initial geometry; capture validation still required')
