"""Compile a five-stage assessment into explicit, verifiable dependencies."""
from copy import deepcopy
from capabilities.benchmark_design.stages import CONTRACT, STAGES, COLLECTION_OPERATIONS


def complete_production_steps(steps):
    steps = deepcopy(steps)
    index = next((i for i, step in enumerate(steps) if step['uses'] == 'templates.produce'), None)
    if index is None:
        raise ValueError('A production assessment needs executable templates.produce and data.package steps')
    if {'accepted_scenes', 'collection', 'annotations'} & {step['id'] for step in steps}:
        raise ValueError('accepted_scenes, collection and legacy annotations are reserved production step IDs')
    steps[index:index] = [
        {'id': 'accepted_scenes', 'uses': 'scene.acceptance', 'artifact_type': 'scene.acceptance',
         'arguments': {'scene_sources': '$inputs.scene_sources'}},
        {'id': 'collection', 'uses': 'collection.validate', 'artifact_type': 'scene.collection',
         'arguments': {'evidence': '$inputs.evidence', 'scene_sources': '$accepted_scenes.scene_sources'}},
    ]
    for step in steps:
        if step['uses'] == 'templates.produce':
            step.setdefault('arguments', {})['evidence'] = '$collection.evidence'
    validate_production_steps(steps)
    return steps


def validate_production_steps(steps):
    """Only semantic operations establish stages; review_stage cannot relabel them."""
    seen, accepted, collected, datasets = set(), set(), set(), set()
    packaged = False
    for step in steps:
        key, operation = step.get('id'), step.get('uses')
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError('Executable step IDs must be unique nonempty strings')
        seen.add(key)
        args = step.get('arguments', {})
        if operation == 'scene.acceptance':
            accepted.add(key)
        elif operation in COLLECTION_OPERATIONS:
            if args.get('scene_sources') not in {'$' + name + '.scene_sources' for name in accepted}:
                raise ValueError('S3 collection, including annotations, must depend on a prior S2 scene.acceptance receipt')
            collected.add(key)
        elif operation == 'templates.produce':
            if args.get('evidence') not in {'$' + name + '.evidence' for name in collected}:
                raise ValueError('S4 questions require prior S2 acceptance and S3 collection.validate; prose is not an execution plan')
            datasets.add(key)
        elif operation == 'data.package':
            if args.get('dataset') not in {'$' + name + '.dataset' for name in datasets}:
                raise ValueError('S5 packaging must depend on the validated S4 dataset')
            packaged = True
        elif operation == 'pipeline':
            # Scene/asset subrecipes keep their established queue and review owner.
            # Dataset stages need explicit bindings to the validated collection.
            if step.get('pipeline_id') not in {'scene.create', 'scene.capture', 'scene.refine', 'scene.extend', 'asset.prepare'}:
                raise ValueError('Expand nested dataset stages so their S2/S3 dependencies are explicit')
    if not (accepted and collected and datasets and packaged):
        raise ValueError('Production needs S2 scene.acceptance, S3 collection.validate, S4 templates.produce and S5 data.package')


def execution_contract():
    return {
        'schema': CONTRACT['schema'],
        'stages': list(STAGES),
        'definitions': deepcopy(CONTRACT['stages']),
        'scene_sources': [{'task_id': 'service-issued sf_*.scene0', 'revision': 0}],
        'default_recipe': 'data.capability inserts S2 scene.acceptance and S3 collection.validate for an assessment; saved evidence.validate steps remain supported',
        'continuation': 'Save S1 and finish its review window first. S2 selects permitted existing scenes or creates and accepts planned scenes; previews validate scene readiness, not dataset quantity. Resume the same run with bindings.scene_sources. After the S2 window, S3 collects real trajectories, sensor observations and states, includes annotations and alignment, then validates bindings.evidence. Reused captures must meet the same collection contract. S4 uses only validated S3 evidence.',
        'review_policy': 'Pending, paused or needs_input reviews block dependent production, including direct service and shell submissions. Reading, inspecting and editing drafts remain available. Only user actions or the saved deadline release a review; the model cannot continue it early.',
        'truth': 'Only service-issued accepted revisions establish scene completion; handwritten acceptance files do not.',
    }
