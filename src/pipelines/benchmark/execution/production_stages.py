"""Receipts for scene selection/creation and collection including annotations."""
from collections import Counter
from pathlib import Path

from storage.artifacts import rows, write
from service_access.production_receipts import SceneReceipts
from capabilities.dataset_authoring.production import bind_record, counts_for, validate_public_media
from capabilities.dataset_authoring.question_writing import validate_spec
from capabilities.dataset_authoring.answer_contracts import validate_answer
from capabilities.dataset_authoring.quality import validate_choice
from capabilities.dataset_authoring.trajectory_evidence import validate_native_episode
from capabilities.dataset_authoring.source_binding import validate_source_binding
from capabilities.task_catalog.benchmark import all_templates


def validate_planned_scenes(accepted, assessment):
    """Keep reused identities and newly captured designs bound to the saved plan."""
    scenes = assessment.get('scenes', {})
    selected = {(row.get('task_id', row.get('id')), row.get('revision'))
                for row in scenes.get('selected_scenes', [])}
    designs = {row['id']: row for row in scenes.get('designs', [])}
    for receipt in accepted:
        identity = (receipt['task_id'], receipt['revision'])
        if identity in selected:
            continue
        if scenes.get('allow_new_scenes') is False:
            raise ValueError('S2 scene is outside the saved user selection')
        design = receipt.get('scene_design') or {}
        if design.get('id') not in designs or designs[design['id']] != design:
            raise ValueError('S2 capture does not match a saved scene design; capture the planned scene or explicitly select an accepted revision')


def scene_acceptance(arguments, directory, environment, client):
    sources = arguments.get('scene_sources') or []
    evidence = arguments.get('evidence')
    if not sources and evidence and Path(evidence).is_file():
        sources = [row.get('provenance', {}) for row in rows(evidence)]
    if not sources:
        return {'state': 'needs_evidence', 'stage': 'S2', 'next':
                'S2 selects permitted accepted scene revisions or creates and accepts the saved scene designs through benchforge_capture/run/refine. Resume this run with bindings.scene_sources=[{task_id,revision}]. Preview captures validate scenes; trajectory/sample counts cannot replace scene acceptance.'}
    unique = {(row.get('task_id'), row.get('revision')): row for row in sources}
    accepted = SceneReceipts(environment['inputs'].get('service_config'), client).require(list(unique.values()))
    assessment = environment.get('_assessment') or {}
    if assessment:
        validate_planned_scenes(accepted, assessment)
    scene_plan = assessment.get('scenes', {})
    expected = (scene_plan.get('target_scene_count') or len(scene_plan.get('selected_scenes', []))
                or len(scene_plan.get('designs', [])) or 1)
    scene_count = len({row['scene_group_id'] for row in accepted})
    if scene_count < expected:
        return {'state': 'needs_evidence', 'stage': 'S2', 'accepted': scene_count, 'required': expected,
                'next': 'Select or create and accept the remaining planned scenes before S3 collection, including annotations.'}
    reference = [{'task_id': row['task_id'], 'revision': row['revision']} for row in accepted]
    receipt = {'schema': 'benchforge.scene-stage/v1', 'state': 'completed', 'stage': 'S2',
               'scenes': accepted, 'scene_sources': reference, 'scene_count': scene_count,
               'required_scene_count': expected}
    path = Path(directory) / 'accepted-scenes.json'
    write(path, receipt)
    return {**receipt, 'artifacts': [{'path': str(path.resolve()), 'title': '场景验收回执', 'stage': 'S2'}]}


def validate_collection(arguments, directory, environment, client, *, legacy=False):
    """Validate actual collected sources; this operation does not acquire missing data."""
    source = arguments.get('evidence')
    if not source or not Path(source).is_file():
        return {'state': 'needs_evidence', 'stage': 'S3', 'next':
                'S3 collects the planned trajectories, sensor observations and states in accepted S2 scenes, including derived annotations and alignment. Reused captures must satisfy the same contract. Save source-bound evidence JSONL and resume this run with bindings.evidence; scene previews alone do not complete collection.'}
    source = Path(source).resolve()
    records = rows(source)
    receipts = SceneReceipts(environment['inputs'].get('service_config'), client)
    approved = receipts.require(arguments['scene_sources'])
    allowed = {(row['task_id'], row['revision']) for row in approved}
    counts = counts_for(environment['inputs'].get('selections', []))
    if not counts:
        raise ValueError('S3 requires explicit per-template item counts')
    templates = {row['id']: row for row in all_templates()}
    totals, groups, versions, episodes, media_files = Counter(), {}, {}, {}, set()
    for record in records:
        origin = record.get('provenance') or {}
        if (origin.get('task_id'), origin.get('revision')) not in allowed:
            raise ValueError('S3 evidence references a scene outside the accepted S2 revision set')
        receipt = receipts.for_record(record)
        split = record.get('split', environment['inputs'].get('split', 'train'))
        group = receipt['scene_group_id']
        accepted_sources = {Path(name).resolve() for name in receipt.get('artifacts', [])}
        scene_root = Path(receipt['source_directory']).resolve()
        capture_root = scene_root / 'capture'
        for media in record.get('media', []):
            file = Path(media)
            file = file.resolve() if file.is_absolute() else (source.parent / file).resolve()
            if not file.is_relative_to(capture_root) or file not in accepted_sources:
                raise ValueError('S3 public observations must come from the accepted scene capture')
            media_files.add(str(file))
        if receipt.get('split') and split != receipt['split']:
            raise ValueError('Evidence partition conflicts with its accepted scene')
        if group in groups and groups[group] != split:
            raise ValueError('One accepted scene cannot be renamed into train/dev/test groups')
        groups[group] = split
        if record.get('template_id') not in counts:
            raise ValueError('S3 annotations must belong to the saved template allocation')
        template = templates[record['template_id']]
        sample = bind_record(template, record, source.parent)
        dependencies = validate_source_binding(template, record, source.parent, receipt, sample['oracle'])
        require_episode = (environment.get('_assessment') or {}).get('annotations', {}).get('require_native_episode', False)
        interactive = template['protocol']['interaction_required']
        require_locomotion = (environment.get('_assessment') or {}).get('annotations', {}).get('require_robot_locomotion', False)
        try:
            episode = validate_native_episode(record, receipt, required=require_episode or interactive or require_locomotion,
                                              interaction=interactive, robot_locomotion=require_locomotion)
        except ValueError as error:
            return {'state': 'needs_evidence', 'stage': 'S3', 'issue': str(error),
                    'next': 'The simulator service has no matching accepted native episode. Use an installed controller and capture endpoint that records synchronized robot states, or mark this capability unavailable in the assessment. Camera paths, keyframe animation and object-push trials cannot substitute for robot locomotion.'}
        if episode:
            episode_id = (receipt['task_id'], receipt['revision'], episode['id'])
            if require_locomotion and episode_id in episodes:
                raise ValueError('Robot locomotion evidence must contain a distinct accepted episode for each allocated item')
            episodes[episode_id] = episode.get('samples', 0)
        presentation = validate_spec(sample.get('question_spec'), sample['parameters'], len(sample['media']))
        validate_answer(template, sample['oracle'])
        validate_choice(template, sample['oracle'], presentation)
        validate_public_media(sample['media'], sample['source_provenance'].get('path'))
        for name in [*dependencies, *sample['media'], *sample['assets']]:
            file = Path(name).resolve()
            versions[str(file)] = [file.stat().st_size, file.stat().st_mtime_ns]
        totals[template['id']] += 1
    shortfall = {key: count - totals[key] for key, count in counts.items() if totals[key] < count}
    value = {'schema': 'benchforge.annotation-stage/v1' if legacy else 'benchforge.collection-stage/v1', 'stage': 'S3',
             'state': 'needs_evidence' if shortfall else 'completed', 'evidence': str(source),
             'evidence_version': [source.stat().st_size, source.stat().st_mtime_ns],
             'source_versions': versions,
             'items': len(records), 'required_items': sum(counts.values()), 'per_template': dict(totals), 'shortfall': shortfall,
             'collection': {'records': len(records), 'scenes': len(groups), 'media_files': len(media_files),
                            'native_episodes': len(episodes), 'native_samples': sum(episodes.values())},
             'annotations': {'items': len(records), 'per_template': dict(totals)},
             'scene_sources': arguments['scene_sources'],
             'require_native_episode': bool((environment.get('_assessment') or {}).get('annotations', {}).get('require_native_episode')),
             'validation_scope': 'accepted scene revisions, collected observations and native episodes where required, annotation source binding, answer schema, public media, allocation and partitions'}
    path = Path(directory) / ('annotations.json' if legacy else 'collection.json')
    write(path, value)
    return {**value, 'annotations_path' if legacy else 'collection_path': str(path.resolve())}


def validate_evidence(arguments, directory, environment, client):
    """Retain the operation and artifact location of already-saved S3 plans."""
    return validate_collection(arguments, directory, environment, client, legacy=True)
