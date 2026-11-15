"""Compile a five-stage assessment into explicit, verifiable dependencies."""
from copy import deepcopy


def complete_production_steps(steps):
    steps = deepcopy(steps)
    index = next((i for i, step in enumerate(steps) if step['uses'] == 'templates.produce'), None)
    if index is None:
        raise ValueError('A production assessment needs executable templates.produce and data.package steps')
    if {'accepted_scenes', 'annotations'} & {step['id'] for step in steps}:
        raise ValueError('accepted_scenes and annotations are reserved production step IDs')
    steps[index:index] = [
        {'id': 'accepted_scenes', 'uses': 'scene.acceptance', 'artifact_type': 'scene.acceptance',
         'arguments': {'scene_sources': '$inputs.scene_sources'}},
        {'id': 'annotations', 'uses': 'evidence.validate', 'artifact_type': 'scene.annotations',
         'arguments': {'evidence': '$inputs.evidence', 'scene_sources': '$accepted_scenes.scene_sources'}},
    ]
    for step in steps:
        if step['uses'] == 'templates.produce':
            step.setdefault('arguments', {})['evidence'] = '$annotations.evidence'
    validate_production_steps(steps)
    return steps


def validate_production_steps(steps):
    """Only semantic operations establish stages; review_stage cannot relabel them."""
    seen, accepted, annotated, datasets = set(), set(), set(), set()
    packaged = False
    for step in steps:
        key, operation = step.get('id'), step.get('uses')
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError('Executable step IDs must be unique nonempty strings')
        seen.add(key)
        args = step.get('arguments', {})
        if operation == 'scene.acceptance':
            accepted.add(key)
        elif operation == 'evidence.validate':
            if args.get('scene_sources') not in {'$' + name + '.scene_sources' for name in accepted}:
                raise ValueError('S3 annotations must depend on a prior S2 scene.acceptance receipt')
            annotated.add(key)
        elif operation == 'templates.produce':
            if args.get('evidence') not in {'$' + name + '.evidence' for name in annotated}:
                raise ValueError('S4 questions require prior S2 acceptance and S3 evidence.validate; prose is not an execution plan')
            datasets.add(key)
        elif operation == 'data.package':
            if args.get('dataset') not in {'$' + name + '.dataset' for name in datasets}:
                raise ValueError('S5 packaging must depend on the validated S4 dataset')
            packaged = True
        elif operation == 'pipeline':
            # Scene/asset subrecipes keep their established queue and review owner.
            # Dataset stages need explicit bindings to the accepted annotations.
            if step.get('pipeline_id') not in {'scene.create', 'scene.capture', 'scene.refine', 'scene.extend', 'asset.prepare'}:
                raise ValueError('Expand nested dataset stages so their S2/S3 dependencies are explicit')
    if not (accepted and annotated and datasets and packaged):
        raise ValueError('Production needs S2 scene.acceptance, S3 evidence.validate, S4 templates.produce and S5 data.package')


def execution_contract():
    return {
        'schema': 'benchforge.production-stages/v1',
        'stages': ['S1', 'S2', 'S3', 'S4', 'S5'],
        'scene_sources': [{'task_id': 'service-issued sf_*.scene0', 'revision': 0}],
        'default_recipe': 'data.capability inserts S2 scene.acceptance and S3 evidence.validate for an assessment',
        'continuation': 'Save S1 before capture. Submit through benchforge_capture/run/refine, then resume the same pipeline with bindings.scene_sources and bindings.evidence.',
        'truth': 'Only service-issued accepted revisions establish scene completion; handwritten acceptance files do not.',
    }
