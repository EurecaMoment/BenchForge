"""Read production acceptance from the service's task record, never a task copy."""
from pathlib import Path
import re

from capabilities.scene_library.source_policy import scene_design_parser
from capabilities.scene_library.episodes import native_episodes


def verified_scene(root, task):
    """Validate a service task snapshot and return its canonical capture identity."""
    task_id, revision = task['id'], task['unit']['revision']
    review = (task.get('result') or {}).get('scene_review') or {}
    receipt = review.get('production_acceptance') or {}
    if (task['state'] != 'SUCCEEDED' or review.get('passed') is not True
            or receipt.get('passed') is not True or receipt.get('enforced') is not True):
        raise ValueError('Scene has no completed service acceptance: ' + task_id)
    root = Path(root).resolve()
    directory = root / task_id / f'revision_{revision}'
    if directory.resolve() != directory or not directory.is_relative_to(root):
        raise ValueError('Accepted scene must remain in its service artifact directory')
    for name in ('program.json', 'capture/report.json', 'capture/evidence.json'):
        file = directory / name
        if (not file.is_file() or file.resolve() != file
                or receipt.get('evidence_versions', {}).get(name)
                != [file.stat().st_size, file.stat().st_mtime_ns]):
            raise ValueError('Service-accepted evidence changed; recapture and review: ' + name)
    for name, version in receipt.get('evidence_versions', {}).items():
        relative = Path(name)
        file = directory / relative
        if (relative.is_absolute() or '..' in relative.parts or file.is_symlink()
                or not file.is_file() or file.resolve() != file
                or version != [file.stat().st_size, file.stat().st_mtime_ns]):
            raise ValueError('Service-accepted artifact changed; recapture and review: ' + name)
    for declaration in (receipt.get('geometry_exception'), receipt.get('scene_brief')):
        if declaration and not scene_design_parser().plan_declaration_current(declaration):
            raise ValueError('Service-accepted scene design changed; recapture and review')
    for name, version in receipt.get('mesh_versions', {}).items():
        file = Path(name)
        if not file.is_file() or version != [file.stat().st_size, file.stat().st_mtime_ns]:
            raise ValueError('Service-accepted mesh changed; recapture and review')
    brief = receipt.get('scene_brief') or {}
    metadata = {'plan_path', 'plan_version', 'authority', 'iteration_rule'}
    return {
        'task_id': task_id, 'run_id': task_id.split('.')[0], 'revision': revision, 'state': 'accepted',
        'scene_id': receipt.get('scene_id'), 'scene_group_id': 'scene-v1:' + task_id,
        'capture_token': receipt.get('capture_token'), 'source_directory': str(directory),
        'split': task['unit'].get('intent', {}).get('split'),
        'scene_design': {key: value for key, value in brief.items() if key not in metadata},
        'plan_path': brief.get('plan_path'),
        'native_episodes': native_episodes(directory, receipt),
        'artifacts': [str(directory / name) for name in receipt['evidence_versions']],
        'authority': 'production-service-task-record',
    }


def accepted_scenes(store, sources):
    if not isinstance(sources, list) or not 1 <= len(sources) <= 1000:
        raise ValueError('scene_sources must contain 1..1000 task_id/revision references')
    snapshots, receipts = {}, []
    for source in sources:
        task_id = source.get('task_id') if isinstance(source, dict) else None
        revision = source.get('revision') if isinstance(source, dict) else None
        if not isinstance(task_id, str) or not re.fullmatch(r'sf_[A-Za-z0-9_-]+\.scene\d+', task_id):
            raise ValueError('Production scenes require a service-issued task_id; local labels are not acceptance')
        if type(revision) is not int or revision < 0:
            raise ValueError('Production scenes require an explicit nonnegative revision')
        run_id = task_id.split('.')[0]
        if run_id not in snapshots:
            snapshots[run_id] = store.snapshot(run_id)
        task = next((row for row in snapshots[run_id]['tasks'] if row['id'] == task_id), None)
        if task is None or task['unit']['revision'] != revision:
            raise ValueError('Scene revision is missing or superseded: ' + task_id)
        receipts.append(verified_scene(store.root, task))
    return {'schema': 'benchforge.accepted-scenes/v1', 'state': 'completed', 'scenes': receipts}
