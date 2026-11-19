"""Read-only domain checks behind every automatic stage-review round."""
from hashlib import sha256
from pathlib import Path

from storage.artifacts import read
from pipelines.benchmark.execution.review_artifacts import require_stage_artifact


# 逐项复核已验证来源文件的大小与时间版本，拒绝检查后被修改的采集或注释。
def _source_versions(outputs):
    count = 0
    for output in outputs:
        result = output['result']
        versions = dict(result.get('source_versions', {}))
        if result.get('evidence_version'):
            versions[result['evidence']] = result['evidence_version']
        for name, version in versions.items():
            file = Path(name)
            if not file.is_file() or [file.stat().st_size, file.stat().st_mtime_ns] != version:
                raise ValueError('Validated collection or annotation source changed: ' + name)
            count += 1
    return count


# 重新检查真实保存的阶段回执及当前计划身份，不接受模型提供的通过标志。
def check_artifact(workspace, session, row):
    """Revalidate actual saved receipts; model-supplied pass flags are ignored."""
    root = Path(workspace).resolve()
    file = (root / row['artifact']).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        raise ValueError('Stage review requires an existing receipt inside this workspace')
    before = file.read_bytes()
    key = require_stage_artifact(workspace, session, row['stage'], str(file))
    if key != row['key']:
        raise ValueError('The saved review identity no longer matches its current artifact')
    checks = ['current_plan_and_stage_identity']
    value = read(file)
    if row['stage'] == 'S1':
        if value.get('schema') != 'benchforge.pipeline/v1' or not isinstance(value.get('steps'), list):
            raise ValueError('S1 requires a saved executable pipeline plan')
        if value.get('assessment') is not None:
            from capabilities.benchmark_design.assessment import validate_assessment
            from pipelines.benchmark.design.production_steps import validate_production_steps
            validate_assessment(value['assessment'], value.get('selections', []), value.get('inputs', {}))
            validate_production_steps(value['steps'], value['inputs'].get('selections', []))
            checks.append('assessment_and_production_contract')
        checks.append('saved_executable_plan')
    else:
        if row['stage'] in ('S4', 'S5'):
            from pipelines.benchmark.execution.delivery_contract import verify_delivery
            plan = read(value['plan'])
            if plan.get('assessment'):
                verify_delivery(plan, value['outputs'], row['stage'])
                checks.append('delivered_items_match_plan_allocation')
        checks.append('all_planned_stage_members_completed')
        checks.append(f'unchanged_collection_sources:{_source_versions(value["outputs"])}')
    if file.read_bytes() != before:
        raise ValueError('Stage artifact changed during its self-check; retry against the saved revision')
    return {'artifact': str(file), 'digest': sha256(before).hexdigest(), 'checks': checks}
