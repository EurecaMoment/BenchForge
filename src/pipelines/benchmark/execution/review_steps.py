"""Collect stage receipts for one composed pipeline review owner."""

from pathlib import Path

from storage.artifacts import read_json as read
from capabilities.benchmark_design.stages import STAGES
from pipelines.benchmark.execution.artifact_graph import binding_roots
from capabilities.benchmark_design.stages import stage_of
from storage.operation_outputs import physical_path, logical_value

# 展开组合流水线的叶子产物，为每个生产阶段确定唯一评审所属运行。
def review_scope(plan, run_root, plan_path):
    """One review owner for the leaf artifacts of a composed production run."""
    members = {stage: [] for stage in STAGES[1:]}
    recipes = None

    # 递归展开嵌套配方与未被后续消费的步骤，将实际产物归入阶段成员。
    def expand(steps, root, prefix=''):
        nonlocal recipes
        consumed = set()
        for step in steps:
            for source in binding_roots(step.get('arguments', {})):
                consumed.add(source)
        for step in steps:
            step_root = root/'steps'/step['id']
            label = prefix+step['id']
            if step['uses'] == 'pipeline':
                nested_root = step_root/'nested-run'
                saved = nested_root/'plan.json'
                if saved.exists():
                    nested_steps = read(saved)['steps']
                else:
                    if recipes is None:
                        from pipelines.catalog import pipeline_files
                        recipes = {recipe['id']: recipe for recipe in pipeline_files()}
                    nested_steps = recipes[step['pipeline_id']]['steps']
                expand(nested_steps, nested_root, label+'/')
            else:
                stage = stage_of(step)
                if stage in members:
                    members[stage].append({'step': step['id'], 'label': label, 'uses': step['uses'],
                        'run': str(root.resolve()), 'result_path': str((step_root/'result.json').resolve()),
                        'terminal': step['id'] not in consumed})

    expand(plan['steps'], run_root)
    return {'run': str(run_root.resolve()), 'plan': str(plan_path), 'members': members, 'continued_stages': []}

# 仅在该阶段所有计划成员均已有成功回执时返回产物集合。
def completed_stage(scope, stage):
    """Receipts are retained, but a review needs every planned stage artifact."""
    members = scope['members'][stage]
    if not members:
        return None
    states = {}
    # The last planned artifact is usually unfinished; check it first so large
    # scene batches do not reread every completed scene on each submission.
    for member in reversed(members):
        root = member['run']
        if root not in states:
            path = physical_path(Path(root)/'state.json')
            states[root] = read(path).get('steps', {}) if path.exists() else {}
        prior = states[root].get(member['step'])
        if not prior or prior['state'] != 'completed':
            return None
    outputs = []
    for member in members:
        result_path = physical_path(member['result_path'])
        if not result_path.is_file():
            return None
        result = read(result_path)
        if member['terminal'] and result.get('state') in ('RUNNING', 'READY'):
            return None
        outputs.append({'step': member['label'], 'uses': member['uses'],
                        'result_path': member['result_path'], 'result': logical_value(result)})
    return outputs
