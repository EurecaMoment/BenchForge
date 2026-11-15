"""A model may present a saved stage receipt, but cannot invent its completion."""
from pathlib import Path

from storage.artifacts import read
from pipelines.benchmark.design.plan_versions import current


def require_stage_artifact(workspace, session, stage, artifact):
    root = Path(workspace).resolve()
    file = (root / artifact).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        raise ValueError('Stage review requires an existing receipt inside this workspace')
    active = current(workspace, session)
    if not active or active.get('pending'):
        raise ValueError('Save and apply the production plan before opening a stage review')
    plan_path = Path(active['plan']).resolve()
    if stage == 'S1':
        if file != plan_path:
            raise ValueError('S1 review must reference the current saved plan')
        saved = read(plan_path)
        return saved.get('stage_review_key', str(plan_path))
    # The runner owns stage aggregation and writes the receipt after all stage
    # members finish. A standalone summary or a count of files is not this receipt.
    from pipelines.benchmark.execution.review_steps import review_scope, completed_stage
    value = read(file)
    run_root = file.parent.parent
    if (file.parent.name != 'reviews' or file.name != stage + '.json'
            or value.get('schema') != 'benchforge.stage-review/v1' or value.get('stage') != stage
            or Path(value.get('plan', '')).resolve() != plan_path
            or not (run_root / 'plan.json').is_file()):
        raise ValueError('Use the pipeline runner to establish an actual ' + stage + ' receipt; a handwritten summary cannot complete a stage')
    plan = read(plan_path)
    if read(run_root / 'plan.json') != plan:
        raise ValueError('Stage receipt belongs to a superseded plan')
    outputs = completed_stage(review_scope(plan, run_root, str(plan_path)), stage)
    if not outputs or outputs != value.get('outputs'):
        raise ValueError('Stage review does not match the completed pipeline outputs')
    return str(run_root.resolve()) + ':stage:' + stage
