"""Read-only domain checks behind every automatic stage-review round."""
from hashlib import sha256
from pathlib import Path

from storage.artifacts import read
from pipelines.benchmark.execution.review_artifacts import require_stage_artifact


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


def check_artifact(workspace, session, row):
    """Revalidate actual saved receipts; model-supplied pass flags are ignored."""
    root = Path(workspace).resolve()
    file = (root / row['artifact']).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        raise ValueError('Stage review requires an existing receipt inside this workspace')
    before = file.read_bytes()
    if row['key'].startswith('scene-repair:'):
        from pipelines.scene_production.review_checks import check_repair_artifact
        checks = check_repair_artifact(file, row['stage'])
    else:
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
                validate_production_steps(value['steps'])
                checks.append('assessment_and_production_contract')
            checks.append('saved_executable_plan')
        else:
            checks.append('all_planned_stage_members_completed')
            checks.append(f'unchanged_collection_sources:{_source_versions(value["outputs"])}')
    if file.read_bytes() != before:
        raise ValueError('Stage artifact changed during its self-check; retry against the saved revision')
    return {'artifact': str(file), 'digest': sha256(before).hexdigest(), 'checks': checks}
