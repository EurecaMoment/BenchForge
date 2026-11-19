"""Keep execution repairs in their S1 window without approving changed requirements."""
from copy import deepcopy
from pathlib import Path


CONTRACT_FIELDS = ('schema', 'pipeline_id', 'objective', 'inputs', 'steps', 'selections',
                   'template_contracts', 'assessment', 'production_contract', 'user_markdown')


# 提取决定生产评审身份的任务契约，排除执行路径及不改变测量要求的修复描述。
def _contract(plan):
    value = {key: deepcopy(plan[key]) for key in CONTRACT_FIELDS if key in plan}
    # Collected receipts and output locations are execution bindings. Their
    # contents still need S2/S3 validation; none of these paths grant acceptance.
    for key in ('evidence', 'scene_sources'):
        value.get('inputs', {}).pop(key, None)
    for design in value.get('assessment', {}).get('scenes', {}).get('designs', []):
        # Descriptive repairs and their audit trail do not alter measurement
        # requirements, object permissions, relations or physical assumptions.
        for key in ('artifact', 'name', 'summary', 'revision_history', 'previous_capture_directory'):
            design.pop(key, None)
    return value


# 仅将角色声明完全一致的一对一对象更名识别为机械修复。
def _object_renames(before, after):
    """Only a one-to-one rename with identical declared roles is mechanical."""
    old, new = before.get('object_roles', []), after.get('object_roles', [])
    if len(old) != len(new):
        return {}
    old_ids = [row.get('object_id') for row in old]
    new_ids = [row.get('object_id') for row in new]
    if (None in old_ids or None in new_ids or len(set(old_ids)) != len(old_ids)
            or len(set(new_ids)) != len(new_ids)):
        return {}
    detail = lambda row: {key: value for key, value in row.items() if key != 'object_id'}
    if any(detail(left) != detail(right) for left, right in zip(old, new)):
        return {}
    return dict(zip(old_ids, new_ids))


# 对齐前后场景声明中的机械对象更名，避免将标识修复误判为任务变更。
def _align_repairs(before, after):
    old = before.get('assessment', {}).get('scenes', {})
    new = after.get('assessment', {}).get('scenes', {})
    renames = {}
    for left, right in zip(old.get('designs', []), new.get('designs', [])):
        if not left.get('id') or left['id'] != right.get('id'):
            continue
        mapping = _object_renames(left, right)
        renames[left['id']] = mapping
        for row in left.get('object_roles', []):
            row['object_id'] = mapping.get(row.get('object_id'), row.get('object_id'))
        for row in left.get('relations', []):
            for key in ('source', 'target'):
                if key in row:
                    row[key] = mapping.get(row[key], row[key])
    old_exceptions, new_exceptions = old.get('geometry_exceptions', []), new.get('geometry_exceptions', [])
    for left, right in zip(old_exceptions, new_exceptions):
        mapping = renames.get(left.get('scene_design_id'), {})
        old_ids = {mapping.get(item, item) for item in left.get('object_ids', [])}
        new_ids = set(right.get('object_ids', []))
        details = lambda row: {key: value for key, value in row.items() if key != 'object_ids'}
        # Narrowing an existing exception cannot silently enlarge its permission.
        # Adding a robot (or any other object) requires a new contract review.
        if details(left) == details(right) and new_ids <= old_ids:
            old_ids = new_ids
        left['object_ids'], right['object_ids'] = sorted(old_ids), sorted(new_ids)


# 递归比较结构值，返回有差异的字段路径，供版本与评审变更说明使用。
def _changes(before, after, prefix=''):
    if type(before) is type(after) and before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        paths = []
        for key in sorted(before.keys() | after.keys()):
            child = f'{prefix}.{key}' if prefix else key
            paths.extend([child] if key not in before or key not in after
                         else _changes(before[key], after[key], child))
        return paths
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        return [path for index, (left, right) in enumerate(zip(before, after))
                for path in _changes(left, right, f'{prefix}.{index}')]
    return [prefix]


# 确保执行修复保留已有采集、机器人及对象要求，实质改任务须通过用户计划编辑。
def _preserve_requirements(before, after):
    old, new = before.get('assessment', {}), after.get('assessment', {})
    for flag in ('require_native_episode', 'require_robot_locomotion'):
        if old.get('annotations', {}).get(flag) is True and new.get('annotations', {}).get(flag) is not True:
            raise ValueError('Execution repairs must preserve ' + flag + '; apply an explicit user plan edit to change the task')
    designs = {row['id']: row for row in new.get('scenes', {}).get('designs', [])}
    for previous in old.get('scenes', {}).get('designs', []):
        current = designs.get(previous['id'], {})
        if not set(previous.get('hard_requirements', [])) <= set(current.get('hard_requirements', [])):
            raise ValueError('Execution repairs must preserve original hard requirements; revise soft descriptions or apply an explicit user plan edit')
        roles = {row['object_id']: row for row in current.get('object_roles', [])}
        for role in previous.get('object_roles', []):
            if role.get('representation') in {'native_asset', 'articulated_robot'}:
                if roles.get(role['object_id'], {}).get('representation') != role['representation']:
                    raise ValueError('Execution repairs cannot remove required asset identity: ' + role['object_id'])


# 由领域契约决定计划谱系和评审身份，复用等价修复的评审并记录实质变更。
def revision_review(plan, target, previous=None, base_plan=None, *, new_task=False, applied_edit=False, draft_edit=False):
    """Return domain-owned lineage and review identity; never accept a caller key."""
    key = str(Path(target).resolve())
    if previous is None:
        return key, {'kind': 'new_task', 'review_reused': False, 'changed_fields': []}
    before, after = _contract(previous), _contract(plan)
    _align_repairs(before, after)
    changed = _changes(before, after)
    source = lambda value: (value.get('authoring_sources', {}).get('request_path')
                            or value.get('authoring_sources', {}).get('assessment_path'))
    different_source = bool(source(previous) and source(plan) and source(previous) != source(plan))
    new_task = new_task or (different_source and not applied_edit)
    if not new_task and not applied_edit and not draft_edit:
        _preserve_requirements(before, after)
    # applies_edit is trusted only after verification against the active pending
    # workbench edit. It must retain any paused state and saved user instruction.
    reuse = not new_task and (not changed or applied_edit)
    if reuse:
        key = previous.get('stage_review_key', str(Path(base_plan).resolve()))
    return key, {'kind': 'new_task' if new_task else 'user_edit' if applied_edit
                 else 'contract_change' if changed else 'execution_repair',
                 'base_plan': str(base_plan), 'review_reused': reuse, 'changed_fields': changed}
