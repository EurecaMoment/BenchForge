"""生产开始后的计划修订边界；执行修复不能隐式替换正在进行的任务。"""
from pathlib import Path

from capabilities.benchmark_design.stages import stage_of
from storage.pipeline_queries import latest_production_execution


def execution_repair_receipt(workspace, session, active, previous, revision, *, user_edit=False):
    """在激活计划前返回原生产的修复入口；真实用户编辑和未开始的计划不受影响。"""
    if not session or not active or user_edit or revision['kind'] == 'execution_repair':
        return None
    execution = latest_production_execution(workspace, session)
    if not execution or execution['state'].get('state') == 'completed':
        return None
    state = execution['state']
    steps = state.get('steps', {})
    if not steps:
        return None  # 仅有 S1 等待记录尚未开始生产，可正常修订计划。
    step = next((row for row in execution['steps'] if steps.get(row['id'], {}).get('state') != 'completed'), None)
    stage = stage_of(step) if step else state.get('stage_review', {}).get('stage', 'S5')
    key = previous.get('stage_review_key') or active['plan']
    same_lineage = key == execution.get('plan_key')
    if not same_lineage:
        stage = 'S2'  # 历史执行属于旧合同，不能把它的下游完成度套到当前计划。
    run = execution.get('run', '') if same_lineage else str(Path(key).parent / 'pipeline_run')
    next_step = ('修复当前阶段的场景程序、资产或证据；不要用 new_task 或重写 S1 消除执行错误。'
                 '场景通过真实验收后，用 benchforge_pipeline_run(plan=plan, run=run, '
                 'bindings={scene_sources:[{task_id,revision}]}) 续跑；S3 证据通过 bindings.evidence 提交。'
                 '只有用户确实改变需求时才通过工作台编辑并应用计划；独立任务使用独立对话。')
    if stage == 'S2' and any('.relations.' in field for field in revision['changed_fields']):
        next_step = ('先用 benchforge_scene_preflight(scene_program_path=...) 检查现有场景草稿。场景可执行且当前审核允许时，'
                     '用 benchforge_capture(scene_program_path=...) 查看实际图像。原生关系仍须按当前简报验收。'
                     '若确需更改简报中的关系，使用当前计划的工作台编辑与应用流程，再按审核回执续跑。')
    return {'state': 'needs_execution_repair', 'stage': stage or 'S2', 'plan': active['plan'],
            'run': run, 'previous_run': execution.get('run', ''),
            'completed': sum(row.get('state') == 'completed' for row in steps.values()) if same_lineage else 0,
            'total_steps': len(previous.get('steps', [])),
            'production_contract': previous.get('production_contract'),
            'review_revision': {**revision, 'applied': False},
            'error': '已有生产尚未完成；本次计划替换未应用，原计划和审核保持不变。',
            'next': next_step,
            'resume': {'tool': 'benchforge_pipeline_run',
                       'arguments': {'plan': active['plan'], **({'run': run} if run else {})}}}
