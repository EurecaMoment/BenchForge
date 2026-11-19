"""Project the active assessment's scene completion onto the workspace catalog."""
from pathlib import Path

from capabilities.benchmark_design.contracts import PIPELINE_SCHEMAS
from capabilities.scene_library.scene_library import main
from capabilities.scene_library.scene_library import read_json


# 依据当前活动评估计划重新计算场景完成状态，失效或待应用计划不沿用旧完成标志。
def current_production_status(previous, items, folder):
    """Recompute scene completion against the active assessment plan."""
    status = previous.get('production_status')
    if not isinstance(status, dict) or not isinstance(status.get('session'), str):
        return None
    try:
        from pipelines.benchmark.design.plan_versions import current
        active = current(folder.parent, status['session'])
        if not active or active.get('pending') or not active.get('plan'):
            return None
        path = Path(active['plan']).resolve()
        stat = path.stat()
        if not path.is_file() or stat.st_size > 2 * 1024 * 1024:
            return None
        version = {'path': str(path), 'bytes': stat.st_size, 'mtime_ns': stat.st_mtime_ns}
        plan = read_json(path, {})
        if plan.get('schema') not in PIPELINE_SCHEMAS or plan.get('version') != 1:
            return None
        scenes = plan['assessment']['scenes']
        target = scenes['target_scene_count']
        selected = {row['id'] for row in scenes['designs']}
        if type(target) is not int or target < 1 or len(selected) < target:
            return None
        reference = {'path': str(path), 'ledger_version': active['version'], 'pending': False,
                     'file_version': version, 'previous_plan': active.get('previous')}
        after = current(folder.parent, status['session'])
        stat = path.stat()
        if (not after or after.get('pending') or after.get('plan') != active['plan'] or
            after.get('version') != active['version'] or after.get('previous') != active.get('previous') or
            [stat.st_size, stat.st_mtime_ns] != [version['bytes'], version['mtime_ns']]):
            return None
        completed = {row.get('scene_design_id') for row in items
                     if row.get('origin_session') == status['session'] and
                     row.get('scene_family') == status.get('scene_family') and
                     row.get('production_eligible') is True and row.get('available') is True and
                     row.get('source_role') == 'accepted_native_revision' and row.get('applied_plan') == reference} & selected
        return {'scene_family': status.get('scene_family'), 'session': status['session'], 'plan': str(path),
                'accepted': len(completed), 'target': target, 'complete': len(completed) >= target,
                'remaining_design_ids': sorted(selected - completed)}
    except Exception:
        # Progress is optional; an unverifiable ledger must not leave a stale pass.
        return None


# 把重新核对的生产状态写入场景目录，无法确认时移除过期状态。
def project_scene_completion(catalog, folder):
    status = current_production_status(catalog, catalog['items'], folder)
    if status is None:
        catalog.pop('production_status', None)
    else:
        catalog['production_status'] = status


if __name__ == '__main__':
    main(project_catalog=project_scene_completion)
