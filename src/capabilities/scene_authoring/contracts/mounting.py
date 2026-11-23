"""SceneProgram's explicit static attachment declaration."""

FIELDS = {
    'target': 'string identifying a different object declared in this scene',
    'method': 'string "static_mount"',
    'axis': 'integer 0, 1 or 2 (X, Y or Z)',
    'object_face': 'string "min" or "max"',
    'target_face': 'string "min" or "max" opposite object_face',
}

# 供目录、规划器和诊断工具复用的机器可读挂载合同。
CONTRACT = {
    'relation': 'mounted_to',
    'field_path': 'objects[].metadata.spatial_attachment',
    'required_fields': list(FIELDS),
    'fields': {
        'target': {'type': 'string', 'constraint': 'different object id declared in this SceneProgram'},
        'method': {'type': 'string', 'allowed': ['static_mount']},
        'axis': {'type': 'integer', 'allowed': [0, 1, 2]},
        'object_face': {'type': 'string', 'allowed': ['min', 'max']},
        'target_face': {'type': 'string', 'allowed': ['min', 'max'],
                        'constraint': 'must differ from object_face'},
    },
    'endpoint_constraints': {
        'source.dynamic': {'type': 'boolean', 'const': False},
        'target.dynamic': {'type': 'boolean', 'const': False},
    },
    'unsupported_declarations': ['support', 'scene_graph', 'affordances'],
}
SCHEMA = ('Mounting and attachment: for a mounted_to scene-brief relation, set '
          'objects[].metadata.spatial_attachment on its source object. Required fields: '
          + ', '.join(f'{key}: {value}' for key, value in FIELDS.items()) + '. '
          'Both source and target must have dynamic=false (JSON boolean). Example: '
          '{"target":"room_shell","method":"static_mount","axis":1,"object_face":"min","target_face":"max"}. '
          'Select the axis and opposite faces from the actual scene geometry. support specifies '
          'supported_by; scene_graph edges and affordances cannot declare a static mount.')


class AttachmentError(ValueError):
    """保留原始目标和每个错误字段，供预检与验收共用。"""

    def __init__(self, object_id, raw, problems):
        missing = [row['field'] for row in problems if row['reason'] == 'missing']
        invalid = [row['field'] for row in problems if row['reason'] == 'invalid']
        self.evidence = {'mount_declaration_present': True, 'field_errors': problems,
                         'missing_fields': missing, 'invalid_fields': invalid,
                         'required_fields': list(FIELDS),
                         'dynamic_requirement': 'source and target dynamic must be exactly false'}
        if isinstance(raw, dict) and 'target' in raw:
            self.evidence['declared_target'] = raw['target']
        detail = ', '.join(f"{row['field']} ({row['reason']}, expected {row.get('expected', 'valid value')})"
                           for row in problems)
        super().__init__(f'{object_id}: invalid metadata.spatial_attachment: {detail}. Required fields: '
                         f'{", ".join(FIELDS)}. Both source and target dynamic must be exactly false '
                         '(JSON boolean).')


def attachment(obj, declared_objects):
    """校验挂载字段和两端物体的静态声明，几何可用性另由预检判断。"""
    metadata = obj.get('metadata', {})
    if not isinstance(metadata, dict):
        return None
    raw = metadata.get('spatial_attachment')
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise AttachmentError(obj['id'], raw, [{'field': 'spatial_attachment', 'reason': 'invalid',
                              'expected': 'object', 'actual': raw}])
    target = raw.get('target')
    known_target = isinstance(target, str) and target in declared_objects
    faces = ('min', 'max')
    axis = raw.get('axis')
    valid = {
        'target': known_target and target != obj['id'],
        'method': raw.get('method') == 'static_mount',
        'axis': type(axis) is int and axis in (0, 1, 2),
        'object_face': raw.get('object_face') in faces,
        'target_face': raw.get('target_face') in faces and raw['target_face'] != raw.get('object_face'),
    }
    problems = []
    for key, passed in valid.items():
        if not passed:
            row = {'field': key, 'expected': FIELDS[key], 'reason': 'missing'}
            if key in raw:
                row.update(reason='invalid', actual=raw[key])
            problems.append(row)
    endpoints = [('source.dynamic', obj)]
    if known_target and target != obj['id']:
        endpoints.append(('target.dynamic', declared_objects[target]))
    for field, endpoint in endpoints:
        if endpoint.get('dynamic') is not False:
            row = {'field': field, 'object_id': endpoint['id'], 'expected': 'JSON boolean false',
                   'reason': 'missing'}
            if 'dynamic' in endpoint:
                row.update(reason='invalid', actual=endpoint['dynamic'])
            problems.append(row)
    if problems:
        raise AttachmentError(obj['id'], raw, problems)
    return raw
