"""Resolve model-declared simplifications from a saved scene production plan."""
import json
import math
from pathlib import Path
import re
from service_access.resource_files import (
    owned_resource_version, read_owned_resource, resolve_owned_resource,
)

_UNRESOLVED_DESIGN = object()
_DECLARATION_METADATA = {'plan_path', 'plan_version', 'authority', 'iteration_rule', 'scene_style'}

DETAIL_FIELDS = ('purpose', 'reason', 'simplifications', 'retained_constraints', 'excluded_claims')


# 受限读取有所有权的JSON资源，同时返回该读取版本。
def _document(path, root=None):
    raw, version = read_owned_resource(path, limit=2 * 1024 * 1024, artifact_root=root)
    return json.loads(raw.decode('utf-8-sig')), version


# 按当前访问范围解析自有资源路径，并按需要求文件存在。
def _resource(path, root=None, *, must_exist=True):
    return resolve_owned_resource(None, root, path, must_exist=must_exist)


# 判断解析后的路径是否位于策略明确排除的任一目录下。
def path_is_excluded(path, policy):
    source = Path(path).resolve()
    return any(source.is_relative_to(Path(root).resolve()) for root in policy.get('excluded_paths', []))


# 递归检查声明是否引用已排除的任务、运行、场景组或文件路径。
def references_excluded_source(value, policy):
    if isinstance(value, dict):
        return any(references_excluded_source(child, policy) for child in value.values())
    if isinstance(value, list):
        return any(references_excluded_source(child, policy) for child in value)
    if not isinstance(value, str):
        return False
    return (
        any(value in policy.get(field, ()) for field in ('excluded_tasks', 'excluded_runs', 'excluded_scene_groups'))
        or Path(value).is_absolute() and path_is_excluded(value, policy)
    )




# 验证设计与几何简化例外的绑定、对象声明及资产表示要求。
def validate_geometry_declarations(scenes):
    from capabilities.scene_authoring.contracts.requirements import validate_asset_requirements
    declarations = scenes.get('geometry_exceptions', [])
    if not isinstance(declarations, list):
        raise ValueError('scenes.geometry_exceptions must be an optional array')
    designs = scenes.get('designs', [])
    if not isinstance(designs, list) or any(not isinstance(row, dict) for row in designs):
        raise ValueError('scenes.designs must contain scene designs')
    design_ids = {row.get('id') for row in designs}
    for index, design in enumerate(designs):
        if design.get('geometry_exceptions') and not any(
                isinstance(row, dict) and row.get('scene_design_id') == design.get('id')
                for row in declarations):
            raise ValueError(f'scenes.designs[{index}].geometry_exceptions is misplaced; '
                             'declare it in scenes.geometry_exceptions with scene_design_id, '
                             'scene_id, object_ids, purpose, reason, simplifications, '
                             'retained_constraints and excluded_claims')
    seen = set()
    for value in declarations:
        if not isinstance(value, dict):
            raise ValueError('Geometry declarations must be objects')
        design, scene = value.get('scene_design_id'), value.get('scene_id')
        if not isinstance(design, str) or design not in design_ids or not isinstance(scene, str) or not scene.strip():
            raise ValueError('Geometry declaration must identify a declared design and exact scene')
        if (design, scene) in seen:
            raise ValueError('Declare one geometry exception for this scene')
        seen.add((design, scene))
        for key in DETAIL_FIELDS:
            if (not isinstance(value.get(key), str) or not value[key].strip() or
                    value[key].strip().casefold() in {'无', 'none', 'todo', 'tbd', '待填写', 'n/a'}):
                raise ValueError('Geometry exception needs concrete ' + key + ' in the scene plan')
        ids = value.get('object_ids')
        if (not isinstance(ids, list) or not ids or
                not all(isinstance(item, str) and item.strip() for item in ids) or len(set(ids)) != len(ids)):
            raise ValueError('Geometry exception must name the exact simplified objects in this scene')
    validate_asset_requirements(scenes)
    return declarations


# 解析场景唯一关联的已保存生产计划，并验证路径、排除策略及设计身份。
def saved_design(program, policy=None):
    references = [row for row in program.get('provenance', {}).get('sources', [])
                  if isinstance(row, dict) and row.get('kind') == 'scene_plan']
    if not references:
        return None
    if len(references) != 1:
        raise ValueError('Reference one saved scene production plan')
    reference = references[0]
    if not isinstance(reference.get('path'), str):
        raise ValueError('Scene plan reference needs the field "path" containing its saved absolute path: '
                         '{"kind":"scene_plan","path":"<returned plan path>",'
                         '"scene_design_id":"<design id>"}. '
                         'Repair provenance.sources in the scene program; do not recreate the plan.')
    path = Path(reference['path'])
    if not path.is_absolute():
        raise ValueError('Scene plan must be an existing saved JSON document')
    if policy and path_is_excluded(path, policy):
        raise ValueError('Removed scene plans cannot declare a new geometry exception')
    plan, _ = _document(path)
    if not isinstance(plan, dict):
        raise ValueError('Reference a saved assessment scene plan')
    assessment = plan.get('assessment', plan)
    if not isinstance(assessment, dict) or assessment.get('schema') != 'benchforge.assessment/v1':
        raise ValueError('Reference a saved assessment scene plan')
    scenes = assessment.get('scenes', {})
    if not isinstance(scenes, dict):
        raise ValueError('Saved scene plan needs scenes')
    validate_geometry_declarations(scenes)
    design = reference.get('scene_design_id')
    if not isinstance(design, str) or design not in {row.get('id') for row in scenes.get('designs', [])}:
        raise ValueError('Scene plan reference must identify its scene design')
    return scenes, next(row for row in scenes['designs'] if row['id'] == design), path


# 读取当前场景精确匹配的几何简化声明，附上计划版本并标明其声明性质。
def geometry_exception(program, policy=None, *, resolved_design=_UNRESOLVED_DESIGN):
    saved = saved_design(program, policy) if resolved_design is _UNRESOLVED_DESIGN else resolved_design
    if not saved:return None
    scenes, design, path = saved
    matched = [row for row in scenes.get('geometry_exceptions', []) if row['scene_design_id'] == design['id'] and row['scene_id'] == program.get('scene_id')]
    if not matched:
        return None
    value = matched[0]
    if not set(value['object_ids']) <= {obj['id'] for obj in program['objects']}:
        raise ValueError('Geometry exception must name the exact simplified objects in this scene')
    return {**value, 'plan_path': str(path.resolve()), 'plan_version': owned_resource_version(path),
            'scene_style': 'simplified_geometry', 'authority': 'model-declared experimental design, not physical evidence'}


_REVIEW_DIRECTIONS = {'left', 'right', 'front', 'back', 'above', 'below'}


# 将空间参考向量校验为三个有限坐标，排除布尔与非数值输入。
def _review_reference_vector(value, name):
    if (not isinstance(value, (list, tuple)) or len(value) != 3 or
            any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value)):
        raise ValueError(name + ' needs three finite coordinates')
    return tuple(float(v) for v in value)


# 计算两个空间参考向量的点积。
def _review_reference_dot(a, b):
    return sum(x*y for x, y in zip(a, b))


# 将非退化空间参考向量归一化为单位方向。
def _review_reference_unit(a, name):
    length = math.sqrt(_review_reference_dot(a, a))
    if length <= 1e-8:
        raise ValueError(name + ' is degenerate')
    return tuple(v/length for v in a)


# 验证文字或结构化空间参考，检查世界向上方向、命名坐标系和对象绑定。
def validate_reference(design, object_ids=None):
    reference = design.get('spatial_reference')
    if reference is None:
        return None
    if isinstance(reference, str):
        if not reference.strip():
            raise ValueError('spatial_reference text must define a concrete reference')
        return reference
    if not isinstance(reference, dict) or reference.get('schema') != 'benchforge.review-reference/v1':
        raise ValueError('spatial_reference needs text or benchforge.review-reference/v1')
    up = _review_reference_unit(_review_reference_vector(reference.get('world_up'), 'world_up'), 'world_up')
    frames = reference.get('frames')
    if not isinstance(frames, list) or not frames:
        raise ValueError('spatial_reference needs explicitly named frames')
    by_id = {}
    for frame in frames:
        if (not isinstance(frame, dict) or not isinstance(frame.get('id'), str) or not frame['id'].strip() or
                frame['id'] in by_id or frame.get('kind') not in {'user', 'world'}):
            raise ValueError('spatial reference frames need distinct IDs and user/world kind')
        keys = ('origin_object_id', 'observer_object_id', 'facing_target_object_id') if frame['kind'] == 'user' else ('origin_object_id',)
        if any(not isinstance(frame.get(k), str) or not frame[k].strip() for k in keys):
            raise ValueError('spatial reference needs explicit source objects')
        if object_ids is not None:
            missing = [(key, frame[key]) for key in keys if frame[key] not in object_ids]
            if missing:
                details = ', '.join(f"{key}={value!r}" for key, value in missing)
                available = ', '.join(sorted(object_ids)) or '(none)'
                raise ValueError(
                    f"spatial reference frame {frame['id']!r} names unavailable object(s): {details}; "
                    f'current program object IDs: {available}'
                )
        if frame['kind'] == 'user':
            if frame.get('front') not in {'toward_observer', 'observer_forward'}:
                raise ValueError('user frame front must distinguish work-surface front from observer forward')
        else:
            front = _review_reference_unit(_review_reference_vector(frame.get('front_world'), 'front_world'), 'front_world')
            right = _review_reference_unit(_review_reference_vector(frame.get('right_world'), 'right_world'), 'right_world')
            if any(abs(value) > 1e-4 for value in (_review_reference_dot(front, right), _review_reference_dot(front, up), _review_reference_dot(right, up))):
                raise ValueError('world reference axes must be orthogonal')
        by_id[frame['id']] = frame
    for relation in design.get('relations', []):
        name, directions = relation.get('reference_frame'), relation.get('expected_direction')
        if name is not None and name not in by_id:
            raise ValueError('spatial relation names an unknown reference frame')
        if directions is not None:
            if (name is None or not isinstance(directions, list) or not directions or
                    any(not isinstance(direction, str) or direction not in _REVIEW_DIRECTIONS for direction in directions) or len(set(directions)) != len(directions) or
                    any(pair <= set(directions) for pair in ({'left','right'}, {'front','back'}, {'above','below'}))):
                raise ValueError('directional relation needs a frame and nonconflicting directions')
            if relation.get('target') != by_id[name]['origin_object_id']:
                raise ValueError('directional relation target must be its reference origin')
    return reference


# 汇总场景简报中的标题、布局、硬性要求、物理假设和空间参考问题。
def validate_scene_brief(design, object_ids=None):
    from capabilities.scene_authoring.contracts.requirements import requirement_stages
    problems = []
    for key, message in (
            ('name', 'Scene brief needs a human-readable title'),
            ('summary', 'Scene brief needs a non-empty description of its intended layout and use')):
        if not isinstance(design.get(key), str) or not design[key].strip():
            problems.append('/' + key + ': ' + message)
    for key in ('hard_requirements', 'physical_assumptions'):
        rows = design.get(key)
        if not isinstance(rows, list) or not rows or any(not isinstance(row, str) or not row.strip() for row in rows):
            problems.append('/' + key + ': Scene brief needs concrete ' + key)
    try:
        requirement_stages(design)
    except (ValueError, KeyError, TypeError) as error:
        problems.append('/requirement_stages: ' + str(error))
    roles = design.get('object_roles')
    if not isinstance(roles, list) or not roles:
        problems.append('/object_roles: Scene brief needs object roles')
    else:
        for index, row in enumerate(roles):
            if (not isinstance(row, dict) or not isinstance(row.get('object_id'), str) or
                    not row['object_id'].strip() or not isinstance(row.get('role'), str) or not row['role'].strip()):
                problems.append(f'/object_roles/{index}: Scene brief object role needs an exact object and useful explanation')
                continue
            if object_ids is not None and row['object_id'] not in object_ids:
                problems.append(f'/object_roles/{index}: Scene brief references a missing object: ' + row['object_id'])
    relations = design.get('relations')
    valid_relations = True
    if not isinstance(relations, list) or not relations:
        problems.append('/relations: Scene brief needs explicit spatial relations')
        valid_relations = False
    else:
        for index, row in enumerate(relations):
            if (not isinstance(row, dict) or row.get('type') not in
                    ('supported_by', 'mounted_to', 'clearance', 'near', 'facing', 'inside') or
                    not isinstance(row.get('description'), str) or not row['description'].strip() or
                    any(not isinstance(row.get(key), str) or not row[key].strip() for key in ('source', 'target'))):
                problems.append(f'/relations/{index}: Scene brief needs concrete typed spatial relations')
                valid_relations = False
                continue
            if object_ids is not None:
                if row['type'] == 'mounted_to' and row['target'] == 'ground' and 'ground' not in object_ids:
                    problems.append(
                        f'/relations/{index}/target: mounted_to requires an actual static scene object, '
                        'such as the declared wall. Implicit ground is a supported_by surface.'
                    )
                elif row['source'] not in object_ids or row['target'] not in object_ids | {'ground'}:
                    problems.append(f'/relations/{index}: Scene brief relation references an unavailable object')
    if valid_relations:
        try:
            validate_reference(design, object_ids)
        except (ValueError, KeyError, TypeError) as error:
            problems.append('/spatial_reference: ' + str(error))
    if problems:
        raise ValueError('Scene brief needs corrections:\n' + '\n'.join(problems))
    return design


# 根据冻结的生产证据校验修订简报继承的硬性要求，允许软性设计调整。
def validate_brief_revision(design, path, root=None):
    """A declared revision inherits hard requirements from frozen production.

    Soft design can change in both directions; original production evidence is
    the authority for the inherited requirement list, not the author's new text.
    """
    history=design.get('revision_history', [])
    if not isinstance(history,list) or any(not isinstance(row,dict) for row in history):
        raise ValueError('Scene brief revision_history must be an array of revision records')
    previous=design.get('previous_capture_directory')
    if not previous and not history:
        return
    if not previous:
        if len(history)>1 or any(isinstance(row,dict) and (row.get('previous_attempt') or
                isinstance(row.get('revision'),int) and row['revision']>1) for row in history):
            raise ValueError('A revised scene brief must reference its previous captured evidence')
        directory=Path(history[-1].get('evidence_directory',''))
    else:
        directory=Path(previous)
    if not directory.is_absolute():raise ValueError('Brief revision needs its prior captured evidence directory')
    directory=_resource(directory,root)
    if not directory.is_dir():raise ValueError('Brief revision needs its prior captured evidence directory')
    file=directory/'scene_brief.json'
    previous_history=[]
    if file.is_file():
        frozen,_=_document(file,root)
        if not set(frozen.get('hard_requirements',[])) <= set(design['hard_requirements']):
            raise ValueError('Scene brief revision must preserve every original hard requirement')
        previous_history=frozen.get('revision_history',[])
        if not isinstance(previous_history,list) or history[:-1]!=previous_history:
            raise ValueError('Scene brief revision must preserve earlier revision records')
    if not history or (previous and history[-1].get('evidence_directory')!=str(directory)):
        raise ValueError('Scene brief revision needs actual prior evidence and explicit changes')
    latest=history[-1]
    changes=latest.get('changes')
    if (type(latest.get('revision')) is not int or latest['revision'] < 1 or
            not isinstance(changes,list) or not changes or
            any(not isinstance(change,str) or not change.strip() for change in changes)):
        raise ValueError('Scene brief revision needs a numbered revision and concrete changes')
    if previous_history and (not isinstance(previous_history[-1],dict) or
            type(previous_history[-1].get('revision')) is not int or
            latest['revision'] <= previous_history[-1]['revision']):
        raise ValueError('Scene brief revision number must advance beyond its previous revision')
    unresolved=latest.get('unresolved')
    if (not isinstance(unresolved,list) or
            any(not isinstance(item,str) or not item.strip() for item in unresolved)):
        raise ValueError('Scene brief revision must record unresolved issues, or an empty array')
    refs=latest.get('evidence_refs')
    if not isinstance(refs,list) or not refs:
        raise ValueError('Scene brief revision needs image and physical evidence references')
    resolved=[]
    for ref in refs:
        if (not isinstance(ref,dict) or not isinstance(ref.get('path'),str) or
                not isinstance(ref.get('observation'),str) or not ref['observation'].strip()):
            raise ValueError('Scene brief evidence references need a relative path and concrete observation')
        evidence_path=Path(ref['path'])
        if evidence_path.is_absolute() or '..' in evidence_path.parts:
            raise ValueError('Scene brief evidence references must stay inside the prior capture')
        source=directory/evidence_path
        owned_resource_version(source,artifact_root=root)
        resolved.append(evidence_path.as_posix())
    if not any(re.fullmatch(r'capture/view_\d+\.png',name) for name in resolved):
        raise ValueError('Scene brief revision must cite an actual captured view')
    if not any(name in {'capture/evidence.json','scene_acceptance.json'} for name in resolved):
        raise ValueError('Scene brief revision must cite a native physical or acceptance receipt')
    if directory.resolve()==path.parent.resolve():raise ValueError('Preserve prior capture evidence in a separate revision')


# 已知父场景存在时要求保留其简报继承关系，防止省略修订信息绕过约束。
def require_prior_brief(brief, directory, root=None):
    """A known captured parent cannot be detached by omitting revision metadata."""
    directory=_resource(directory,root,must_exist=False)
    frozen_path = directory/'scene_brief.json'
    if not frozen_path.is_file():
        return
    previous = brief.get('previous_capture_directory')
    if previous and Path(previous).resolve() == directory.resolve():
        return
    frozen, _ = _document(frozen_path, root)
    # Execution-only repairs inherit the unchanged brief from the known parent.
    # A change to the brief still needs explicit revision and prior evidence.
    if brief.get('id') and isinstance(frozen, dict) and (
        {key: value for key, value in brief.items() if key not in _DECLARATION_METADATA} ==
        {key: value for key, value in frozen.items() if key not in _DECLARATION_METADATA}
    ):
        return
    raise ValueError('Scene revision must reference the frozen brief of its captured parent')


# 解析已准入的冻结场景目录，即使尚未产生采集也允许恢复该输入。
def capture_retry_directory(root, task_id, revision=None):
    """Admitted frozen input can be recovered before any capture exists."""
    if not re.fullmatch(r'sf_[a-zA-Z0-9_-]+\.scene\d+',str(task_id)):
        raise ValueError('Unreviewed scene retries require a valid scene task')
    task_directory=_resource(Path(root)/task_id,root)
    folders=[p for p in task_directory.glob('revision_*') if p.is_dir() and p.name[9:].isdigit()]
    if revision is not None:
        folders=[p for p in folders if p.name=='revision_'+str(revision)]
    if not folders:raise ValueError('Unreviewed scene retries have no frozen admitted program')
    directory=max(folders,key=lambda p:int(p.name[9:]))
    admission=directory/'scene_admission.json'
    if (not (directory/'program.json').is_file() or not admission.is_file() or
            _document(admission,root)[0].get('passed') is not True):
        raise ValueError('Unreviewed scene retries require an admitted frozen program')
    return directory


# 解析重审目录，并要求其中已有准入的原生采集回执。
def review_retry_directory(root, task_id):
    directory=capture_retry_directory(root,task_id)
    report=directory/'capture/report.json'
    if not report.is_file() or _document(report,root)[0].get('status')!='captured':
        raise ValueError('Unreviewed scene retries require an admitted native capture')
    return directory


# 比较计划版本或场景设计快照，判断既有验收是否仍适用于当前设计。
def plan_declaration_current(declaration):
    """Reuse acceptance after edits that do not change this scene's design.

    File versions remain the cheap fast path. A changed plan is compared with
    the saved scene snapshot directly; formatting, scoring and other scenes
    must not invalidate unchanged native capture evidence.
    """
    try:
        path = Path(declaration['plan_path'])
        if declaration.get('plan_version') == owned_resource_version(path):
            return True
        plan, _ = _document(path)
        scenes = plan.get('assessment', plan).get('scenes', {})
        if 'scene_design_id' in declaration:
            values = scenes.get('geometry_exceptions', [])
            saved = next((row for row in values if row.get('scene_design_id') == declaration['scene_design_id']
                          and row.get('scene_id') == declaration.get('scene_id')), None)
        else:
            saved = next((row for row in scenes.get('designs', []) if row.get('id') == declaration.get('id')), None)
        expected = {key: value for key, value in declaration.items() if key not in _DECLARATION_METADATA}
        return saved is not None and saved == expected
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return False


# 从已保存设计解析并验证场景简报，保留父场景要求和计划来源。
def scene_brief(program, policy=None, root=None, *, resolved_design=_UNRESOLVED_DESIGN):
    saved = saved_design(program, policy) if resolved_design is _UNRESOLVED_DESIGN else resolved_design
    if not saved:
        if policy and (policy.get('require_scene_brief') or policy.get('enforce_realism')):
            raise ValueError('Save a titled, detailed scene brief in the production plan before scene submission')
        return None
    _, design, path = saved
    if not ((policy or {}).get('require_scene_brief') or (policy or {}).get('enforce_realism')) and 'summary' not in design:return None
    try:
        validate_scene_brief(design, {obj['id'] for obj in program['objects']})
    except ValueError as error:
        raise ValueError(
            f"Saved scene plan {path} design {design['id']!r} is incompatible with this program: {error}"
        ) from error
    if root:
        for source in program.get('provenance',{}).get('sources',[]):
            if not isinstance(source,dict) or source.get('kind')!='original_scene':continue
            task=source.get('task_id','');revision=source.get('revision',0)
            if re.fullmatch(r'sf_[a-zA-Z0-9_-]+\.scene\d+',str(task)) and type(revision) is int and revision>=0:
                require_prior_brief(design,Path(root)/task/('revision_'+str(revision)),root)
    validate_brief_revision(design,path,root)
    return {**design, 'plan_path': str(path.resolve()), 'plan_version': owned_resource_version(path,artifact_root=root),
            'authority': 'versioned scene design, not captured physical truth',
            'iteration_rule': 'repair scene to brief; revise omissions from actual evidence; preserve user hard requirements and failed evidence'}
