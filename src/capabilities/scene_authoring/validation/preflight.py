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
from capabilities.scene_authoring.validation.support_relations import (
    relation_requires_measurement, separated_support_bounds, support_path,
)
from capabilities.scene_authoring.contracts.scene_design import geometry_exception, scene_brief, saved_design
from capabilities.scene_authoring.validation.diagnostics import SceneDiagnostics, ScenePreflightError
from capabilities.scene_authoring.contracts.mounting import attachment
from capabilities.scene_authoring.validation.policy import check_references, detailed_construction, policy_for, structure
from capabilities.scene_authoring.validation.asset_policy import PROXY_KINDS, native_asset_requirement, registered_mesh_status


# 判断三维点是否位于给定中心和尺寸的轴对齐范围内。
def _inside(point: list[float], center: list[float], size: list[float]) -> bool:
    return all(abs(point[i] - center[i]) <= size[i] / 2 for i in range(3))


# 检查相机与房间位置约束，保留显式外部相机声明并报告垂直越界。
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


# 仅返回场景字段中的列表记录，其他类型视为空以继续批量诊断。
def _records(program, field):
    value = program.get(field, [])
    return value if isinstance(value, list) else []


# 判断指定路径或其子字段是否已经存在合同错误。
def _invalid_at(issues, path):
    return any(row['path'] == path or row['path'].startswith(path + '/') for row in issues)


# 筛选可安全诊断的独立几何组件，保持原始提交完全不变。
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


# 独立检查保存计划、几何例外和场景简报，并记录依赖失败导致的跳过项。
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


# 检查前景资产、简化声明及静态安装关系，收集对象级准入问题。
def _object_admission(root, program, objects, indexes, policy, declaration, brief, declared,
                      diagnostics, allow_generated):
    by_id = {obj['id']: obj for obj in objects}
    declared_objects = {obj['id']: obj for obj in _records(program, 'objects')
                        if isinstance(obj, dict) and isinstance(obj.get('id'), str)}
    simplified = set(declaration['object_ids']) if declaration else set()
    mounts = {}
    registered_mesh_objects = set()
    if policy and not any(not structure(obj) for obj in objects) and not diagnostics.skipped:
        diagnostics.add('foreground_missing', '/objects',
                        'Realistic scenes need recognizable foreground assets', basis='scene_policy')
    for obj in objects:
        key = obj['id']
        path = f'/objects/{indexes[key]}'
        metadata_valid = isinstance(obj.get('metadata', {}), dict)
        if metadata_valid:
            ok, mount = diagnostics.attempt('static_mount', path + '/metadata/spatial_attachment',
                lambda: attachment(obj, declared_objects), object_ids=[key])
        else:
            ok, mount = False, None
            diagnostics.skip('static_mount', path + '/metadata/spatial_attachment',
                             'metadata contract is invalid')
        if not ok:
            if metadata_valid:
                details = diagnostics.issues[-1].get('evidence', {})
                target = details.get('declared_target')
                details['geometry_target_available'] = isinstance(target, str) and target in by_id
        elif mount and mount['target'] not in by_id:
            diagnostics.skip('static_mount_geometry', path + '/metadata/spatial_attachment/target',
                             f"Target '{mount['target']}' is declared but unavailable to geometry checks "
                             'because its object record did not pass scene admission; mount geometry and '
                             'scene-brief physical alignment were not evaluated',
                             target_id=mount['target'], target_declared=True,
                             target_admitted=False)
        if ok:
            mounts[key] = mount
        kind = obj['kind']
        mesh_status = registered_mesh_status(root, obj.get('asset_id')) if kind == 'mesh' else None
        generated_mesh = mesh_status == 'generated'
        if mesh_status in ('generated', 'imported') and not native_asset_requirement(obj):
            registered_mesh_objects.add(key)
        if policy and structure(obj) and (kind == 'generated' or generated_mesh):
            diagnostics.add('architectural_asset_required', path + '/kind',
                'Architectural structure and exact openings require native construction or an authored architectural asset',
                basis='scene_asset_policy', object_ids=[key])
        if not policy or structure(obj):
            continue
        specialist_need = native_asset_requirement(obj)
        generated_candidate = kind == 'generated' and allow_generated
        if key not in simplified and specialist_need and (
                kind in PROXY_KINDS or generated_candidate or generated_mesh):
            diagnostics.add('specialist_asset_required', path + '/kind',
                'This object requires a dedicated imported or registered asset for ' + specialist_need,
                basis='scene_asset_policy', object_ids=[key])
        if declared and key not in simplified:
            if (kind not in ASSETS and kind not in PROXY_KINDS | {'mesh', 'generated', 'native'}
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
                'Declared initial bounds differ from nominal ground Z=0. Inspect actual floor contact or attachment in capture.',
                basis='declared_support', severity='warning', object_ids=[key], base_z_m=obj['base_z'])
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
                    if role['representation'] == 'native_asset' and obj['id'] in registered_mesh_objects:
                        continue
                    message = ('Use a catalog asset or an authorized native upload for this role.'
                               if role['representation'] == 'articulated_robot' or native_asset_requirement(obj)
                               else 'Use a catalog asset, an authorized native upload, or kind=mesh with a registered generated or imported asset_id.')
                    diagnostics.add('required_native_asset', f'/objects/{indexes[obj["id"]]}/kind',
                        message, basis='saved_scene_plan', object_ids=[obj['id']])
        for index, relation in enumerate(brief['relations']):
            obj = by_id.get(relation['source'])
            if obj is None:
                diagnostics.skip('brief_relation', f'/scene_brief/relations/{index}',
                                 'source object contract or support component is invalid')
                continue
            expected = relation['target']
            path = f'/objects/{indexes[obj["id"]]}'
            if relation['type'] == 'mounted_to':
                if obj['id'] not in mounts:
                    # 字段错误已由挂载校验报告，避免再把非法声明误报为未填写。
                    continue
                mount = mounts[obj['id']]
                actual = mount['target'] if mount else None
                details = {'mount_declaration_present': mount is not None,
                           'mismatch_kind': 'missing_mount_declaration' if mount is None else 'target_mismatch'}
                path += '/metadata/spatial_attachment'
                message = (f"{obj['id']}: declare the static mount to '{expected}' here. Read the "
                           "mounting contract in benchforge_scene_catalog (section=contracts, item_id=mounting)."
                           if mount is None else
                           f"{obj['id']}: declared mount target '{actual}' differs from scene brief target '{expected}'.")
            elif relation['type'] == 'supported_by':
                declared_path = support_path(by_id, obj['id'], expected)
                if len(declared_path) > 1 and declared_path[-1] == expected:
                    continue
                actual = obj['support']
                details = {'mismatch_kind': 'support_path_unconfirmed', 'support_path': declared_path}
                path += '/support'
                message = (f"{obj['id']}: the declared support path does not establish '{expected}'. "
                           'Review the actual support surface and assembly in captured views.')
            else:
                continue
            if actual != expected:
                diagnostics.add('scene_brief_relation_mismatch', path, message,
                    basis='saved_scene_plan', object_ids=[obj['id']] + ([expected] if expected in by_id else []),
                    severity='warning', relation_index=index, expected_target=expected,
                    declared_target=actual, review_required='captured_view_and_native_relation_check',
                    **details)
    return mounts, simplified


# 使用只读网格预览检查真实尺寸、支撑和潜在重叠，汇总几何准入问题。
def _geometry_diagnostics(root, program, objects, indexes, mounts, simplified,
                          policy, allow_generated, diagnostics):
    cache = {}
    assets_root = Path(root) / 'generated_assets'
    resolved_assets_root = assets_root.resolve()
    simplified_assets = {obj.get('asset_id') for obj in objects if obj['id'] in simplified}

    # 受限读取并缓存登记网格，生产策略下拒绝未经声明的原始占位几何。
    def mesh(asset_id):
        if asset_id not in cache:
            path = assets_root / asset_id / 'mesh.json'
            resolved_path = path.resolve()
            if not resolved_path.is_relative_to(resolved_assets_root):
                raise ValueError('asset path escapes registry')
            try:
                from service_access.access_scope import current_access
                access = current_access()
                if access is not None:
                    from capabilities.asset_generation.asset_access import registered_asset_path
                    path = registered_asset_path(root, asset_id, 'mesh.json', access=access)
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
    # Imported USD dimensions are estimates. Only compare fixed geometry authored by this adapter.
    static_bounds = {row['object_id']: (bounds['min'], bounds['max'])
                     for row in preview['objects']
                     if (bounds := row.get('initial_world_bounds_m'))
                     and not by_id[row['object_id']]['dynamic']
                     and row['kind'] in {'box', 'sphere', 'cylinder', 'container', 'composite', 'mesh'}}
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
        support = obj['support']
        gap = None
        if policy and key in mounts and not mounts[key] and key in static_bounds and support in static_bounds:
            gap = separated_support_bounds(static_bounds[key], static_bounds[support])
        if gap is not None:
            diagnostics.add('static_support_gap', row['path'] + '/support',
                f"The authored static bounds are separated by at least {gap:.3f} m. "
                "Check the declared support, placement or intended mounting.",
                basis='initial_source_geometry', object_ids=[key, support], gap_m=gap)
        elif not structure(obj) and not mounts.get(key) and faces and not any(
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


def _task_geometry_reviews(issues, objects, brief):
    """Link geometry issues to the declared support, clearance or measurement they concern."""
    issues_by_pair = {}
    for issue in issues:
        if issue['code'] in {'static_support_gap', 'support_contact_unverified', 'initial_bounds_overlap'}:
            issues_by_pair.setdefault(frozenset(issue['object_ids']), []).append(issue['id'])
    by_id = {obj['id']: obj for obj in objects}
    groups = []
    for index, relation in enumerate((brief or {}).get('relations', [])):
        kind, source, target = relation['type'], relation['source'], relation['target']
        if kind not in {'supported_by', 'clearance'} and not relation_requires_measurement(relation):
            continue
        path = support_path(by_id, source, target) if kind == 'supported_by' else [source, target]
        matches = [issue_id for pair in zip(path, path[1:])
                   for issue_id in issues_by_pair.get(frozenset(pair), ())]
        if matches:
            groups.append({'relation_index': index, 'type': kind, 'source': source, 'target': target,
                           'issue_ids': matches})
    return groups


# 批量诊断所有可安全检查的独立组件，不启动采集、生成或修改提交。
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
    mounts, simplified = _object_admission(root, program, objects, indexes, policy, declaration,
                                            brief, declared, diagnostics, allow_generated)
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
    report = diagnostics.finish(program, enforced=bool(policy),
        scene_id=program.get('scene_id'),
        scene_style='simplified_geometry' if declaration else 'realistic_assets' if policy else 'unconstrained',
        scene_style_basis='saved declaration or production policy; not verified appearance',
        geometry_exception=declaration, scene_brief=brief, geometry_preview=geometry,
        cameras=camera_checks,
        scope='contracts, declared plans, source assets and initial geometry; capture validation still required')
    report['task_geometry_reviews'] = _task_geometry_reviews(report['issues'], objects, brief)
    return report
