"""Declarative robot action validation independent of the simulator adapter."""
import math


# 验证机器人推物动作的目标、位姿、路点、观察和接触声明。
def validate_robot_action(action, objects):
    required = {'id', 'action', 'object_id', 'robot_id', 'robot_base_position', 'waypoints'}
    optional = {'robot_base_orientation_wxyz', 'end_effector_orientation_wxyz',
                'witness_object_ids', 'observe_seconds', 'camera_id', 'recording', 'contact_object_ids'}
    if not required <= set(action) <= required | optional:
        raise ValueError('robot_push needs id, object_id, robot_id, robot_base_position and waypoints')
    if not objects[action['object_id']]['dynamic']:
        raise ValueError('robot_push needs a dynamic target')
    # 校验固定长度有限向量，四元数还必须为单位wxyz形式。
    def vector(value, length):
        if not isinstance(value, list) or len(value) != length or not all(type(v) in (int, float) and math.isfinite(v) for v in value):
            raise ValueError('invalid robot vector')
        if length == 4 and abs(sum(v*v for v in value)-1) > .001:
            raise ValueError('robot orientation must be a unit wxyz quaternion')
    vector(action['robot_base_position'], 3)
    for key in ['robot_base_orientation_wxyz', 'end_effector_orientation_wxyz']:
        if key in action: vector(action[key], 4)
    if not isinstance(action['waypoints'], list) or not action['waypoints']:
        raise ValueError('robot_push needs at least one end-effector waypoint')
    for waypoint in action['waypoints']:
        if set(waypoint) != {'id', 'position', 'duration_s'}:
            raise ValueError('robot waypoint needs id, position and duration_s')
        vector(waypoint['position'], 3)
        duration = waypoint['duration_s']
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
            raise ValueError('robot waypoint duration_s must be positive')
    witnesses = action.get('witness_object_ids', [])
    if not isinstance(witnesses, list) or len(set(witnesses)) != len(witnesses):
        raise ValueError('robot witness_object_ids must be unique object ids')
    if any(w == action['object_id'] or w not in objects or not objects[w]['dynamic'] for w in witnesses):
        raise ValueError('robot witnesses must name other dynamic objects')
    contacts = action.get('contact_object_ids', [])
    if not isinstance(contacts, list) or len(set(contacts)) != len(contacts) or any(c == action['object_id'] or c not in objects for c in contacts):
        raise ValueError('contact_object_ids must name distinct other scene objects')
    observe = action.get('observe_seconds', 1.5)
    if type(observe) not in (int, float) or not math.isfinite(observe) or observe <= 0:
        raise ValueError('robot observe_seconds must be positive')


# 按120Hz累加机器人准备、路点和观察时长所需仿真步数。
def robot_step_count(action):
    return 120 + sum(math.ceil(w['duration_s']*120) for w in action['waypoints']) + math.ceil(action.get('observe_seconds', 1.5)*120)
