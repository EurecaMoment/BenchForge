"""Stable run placement and conservative rebinding of equivalent plan revisions."""
from pathlib import Path

from pipelines.benchmark.design.plan_review import revision_review
from storage.artifacts import read
from storage.operation_outputs import logical_value
from service_access.access_scope import current_access
from storage.pipeline_queries import latest_production_execution


# 受审计划的默认运行归属原评审计划目录，重复调用不再按工具调用另建运行。
def run_root(args, directory):
    if args.get('run'):
        return Path(args['run']).resolve()
    if args.get('_dsh_context', {}).get('session_id') and not args.get('_review_nested'):
        plan = read(Path(args['plan']).resolve())
        key = plan.get('stage_review_key')
        if key and plan.get('production_contract') == 'benchforge.production-stages/v1':
            root = Path(key).resolve().parent
            if not root.is_relative_to(Path(args['_workspace']).resolve()):
                raise ValueError('The reviewed plan must belong to this conversation workspace')
            if current_access() is not None:
                existing = latest_production_execution(args['_workspace'], args['_dsh_context']['session_id'])
                if existing and existing.get('plan_key') == key and existing.get('run'):
                    return Path(existing['run'])
            return root / 'pipeline_run'
    return Path(directory) / 'pipeline_run'


# 仅允许同一审核谱系的等价修订续接；已执行结果所依赖的输入发生变化时保留旧运行。
def validate_rebinding(previous, plan, state):
    if previous is None or logical_value(previous) == logical_value(plan):
        return
    before, after = logical_value(previous), logical_value(plan)
    key = before.get('stage_review_key')
    same_review = bool(key) and key == after.get('stage_review_key')
    _, change = revision_review(after, key or 'plan.json', before, key or 'plan.json')
    if not same_review or not change['review_reused']:
        raise ValueError('Plan requirements changed; create a new pipeline run to retain prior evidence')
    fields = ('pipeline_id', 'objective', 'inputs', 'steps', 'selections', 'template_contracts',
              'assessment', 'production_contract')
    execution_changed = any(before.get(field) != after.get(field) for field in fields)
    completed = any(row.get('state') == 'completed' for row in state.get('steps', {}).values())
    if execution_changed and completed:
        raise ValueError('The repair changes inputs used by completed steps; use an explicit new run '
                         'to retain prior evidence and revalidate the revised scene')
