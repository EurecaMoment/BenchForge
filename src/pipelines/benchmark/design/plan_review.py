"""Keep execution repairs in their S1 window without approving changed requirements."""
from copy import deepcopy
from pathlib import Path


CONTRACT_FIELDS = ('schema', 'pipeline_id', 'objective', 'inputs', 'steps', 'selections',
                   'template_contracts', 'assessment', 'production_contract', 'user_markdown')


def _contract(plan):
    value = {key: deepcopy(plan[key]) for key in CONTRACT_FIELDS if key in plan}
    # Collected receipts and output locations are execution bindings. Their
    # contents still need S2/S3 validation; none of these paths grant acceptance.
    for key in ('evidence', 'scene_sources'):
        value.get('inputs', {}).pop(key, None)
    for design in value.get('assessment', {}).get('scenes', {}).get('designs', []):
        design.pop('artifact', None)
    return value


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


def revision_review(plan, target, previous=None, base_plan=None, *, new_task=False, applied_edit=False):
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
    # applies_edit is trusted only after verification against the active pending
    # workbench edit. It must retain any paused state and saved user instruction.
    reuse = not new_task and (not changed or applied_edit)
    if reuse:
        key = previous.get('stage_review_key', str(Path(base_plan).resolve()))
    return key, {'kind': 'new_task' if new_task else 'user_edit' if applied_edit
                 else 'contract_change' if changed else 'execution_repair',
                 'base_plan': str(base_plan), 'review_reused': reuse, 'changed_fields': changed}
