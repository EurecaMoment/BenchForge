"""Validate saved repair artifacts without calling a model or simulator."""
from storage.artifacts import read


def check_repair_artifact(file, stage):
    if stage == 'S1' and file.name == 'scene-plan.json':
        from capabilities.scene_authoring.validation.program import validate_program
        from capabilities.scene_authoring.contracts.scene_design import validate_scene_brief
        program = read(file.parent / 'program.json')
        validate_program(program)
        designs = read(file)['assessment']['scenes']['designs']
        if not designs:
            raise ValueError('A repair plan must retain its scene requirements')
        for design in designs:
            validate_scene_brief(design, {item['id'] for item in program['objects']})
        return ['saved_repair_program', 'scene_requirements_and_object_bindings']
    if stage == 'S2' and file.name == 'scene_acceptance.json':
        from capabilities.scene_authoring.validation.acceptance import require_accepted
        require_accepted(file.parents[2], file.parent)
        return ['service_scene_acceptance_and_current_evidence']
    raise ValueError('Unsupported scene repair review artifact')
