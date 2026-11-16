"""Scene evidence scope and explicit asset identity requirements."""

STAGES = {'S2', 'S3', 'S4', 'S5'}
REPRESENTATIONS = {'native_asset', 'articulated_robot'}


def requirement_stages(design):
    requirements = design.get('hard_requirements', [])
    stages = design.get('requirement_stages', ['S2'] * len(requirements))
    if (not isinstance(stages, list) or len(stages) != len(requirements)
            or any(stage not in STAGES for stage in stages)):
        raise ValueError('requirement_stages must assign S2, S3, S4 or S5 to each hard requirement')
    return stages


def scene_requirements(design):
    return [{'index': index, 'requirement': text}
            for index, (text, stage) in enumerate(zip(design['hard_requirements'], requirement_stages(design)))
            if stage == 'S2']


def validate_asset_requirements(scenes, *, robot_locomotion=False):
    robots = []
    for design in scenes.get('designs', []):
        roles = design.get('object_roles', [])
        if not isinstance(roles, list):
            raise ValueError('object_roles must be an array of object roles')
        required = set()
        for role in roles:
            if not isinstance(role, dict):
                raise ValueError('object_roles entries must be objects')
            representation = role.get('representation')
            if representation is None:
                continue
            if not isinstance(representation, str) or representation not in REPRESENTATIONS:
                raise ValueError('object_roles.representation must be native_asset or articulated_robot')
            if not isinstance(role.get('object_id'), str) or not role['object_id']:
                raise ValueError('Required asset identity needs an exact object_id')
            required.add(role['object_id'])
            if representation == 'articulated_robot':
                robots.append(role['object_id'])
        for declaration in scenes.get('geometry_exceptions', []):
            if declaration.get('scene_design_id') == design.get('id'):
                conflict = required.intersection(declaration.get('object_ids', []))
                if conflict:
                    raise ValueError('Geometry exceptions cannot replace required native assets: ' + ', '.join(sorted(conflict)))
    if robot_locomotion and scenes.get('designs') and not robots:
        raise ValueError('Robot locomotion needs an object role with representation=articulated_robot; a label or joints metadata is not an imported robot')
