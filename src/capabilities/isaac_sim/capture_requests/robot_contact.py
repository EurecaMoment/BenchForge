"""Declarative Franka contact actions and simulator-only outcome measurement."""
import math
from capabilities.scene_authoring.contracts.robot_actions import (
    validate_robot_action as validate_robot_action, robot_step_count as robot_step_count,
)


def object_contact_filters(entity):
    # Tensor filters each name one body/shape. A wildcard over a static assembly
    # can match several entries and prevent the contact view from initializing.
    return [entity['prim_path']] if entity['dynamic'] else entity['collision']['paths']


def contact_observation(step, dt, phase, target_position, contact_positions, forces, robot_filter_count, object_filter_counts=None):
    """Keep robot columns separate from named scene-object contact columns."""
    objects={};offset=robot_filter_count
    for name,position in contact_positions.items():
        count=object_filter_counts[name] if object_filter_counts is not None else 1
        objects[name]={'position':position,'force_on_target_N':[sum(f[axis] for f in forces[offset:offset+count]) for axis in range(3)]}
        offset+=count
    return {'step':step, 'timestamp_sim':step*dt, 'phase':phase, 'target_position':target_position,
            'robot_contact_forces_N':forces[:robot_filter_count],
            'objects':objects}


def summarize_object_contacts(trace):
    """Observed contact and motion, without imposing a manipulation objective."""
    result = {}
    for name, initial in trace[0]['objects'].items():
        magnitudes = [math.sqrt(sum(v*v for v in row['objects'][name]['force_on_target_N'])) for row in trace]
        nonzero = [i for i, force in enumerate(magnitudes) if force > 0]
        final = trace[-1]['objects'][name]['position']
        result[name] = {'nonzero_contact_steps':len(nonzero), 'peak_contact_force_N':max(magnitudes),
                        'first_nonzero_contact_step':trace[nonzero[0]]['step'] if nonzero else None,
                        'last_nonzero_contact_step':trace[nonzero[-1]]['step'] if nonzero else None,
                        'displacement_vector_m':[a-b for a,b in zip(final,initial['position'])]}
    return result


def evaluate_robot_trajectory(samples):
    """Measure contact; a push must retain its support height even if upright."""
    norm = lambda values: math.sqrt(sum(v*v for v in values))
    distance = lambda a,b: norm([x-y for x,y in zip(a,b)])
    contacts = [i for i,r in enumerate(samples) if max((norm(f) for f in r['robot_contact_forces_N']), default=0) > .05]
    first = contacts[0] if contacts else len(samples)-1
    initial, final = samples[0], samples[-1]
    drift = max(distance(r['position'], initial['position']) for r in samples[:first+1])
    witness_moves = {key: distance(final['witnesses'][key], value) for key,value in initial['witnesses'].items()}
    _,x,y,_ = final['orientation_wxyz']
    tilt = math.degrees(math.acos(max(-1., min(1., 1-2*(x*x+y*y)))))
    translation = distance(final['position'], initial['position'])
    vertical = max(abs(r['position'][2]-initial['position'][2]) for r in samples)
    checks = {'target_moved':translation >= .04, 'robot_contact_measured':len(contacts) >= 3,
              'no_early_drift':drift <= .005,
              'witnesses_stationary':all(value <= .01 for value in witness_moves.values()),
              'target_upright':tilt <= 20, 'target_height_maintained':vertical <= .02}
    return {'success':all(checks.values()), 'checks':checks, 'translation_m':translation,
            'target_displacement_vector_m':[a-b for a,b in zip(final['position'],initial['position'])],
            'robot_contact_sample_count':len(contacts), 'pre_contact_drift_m':drift,
            'peak_filtered_robot_force_N':max(norm(f) for r in samples for f in r['robot_contact_forces_N']),
            'witness_displacements_m':witness_moves, 'final_target_axis_tilt_deg':tilt,
            'max_vertical_displacement_m':vertical,
            'first_contact_step':samples[first]['step'] if contacts else None,
            'criteria':{'min_translation_m':.04,'contact_force_threshold_N':.05,'min_contact_samples':3,
                        'max_pre_contact_drift_m':.005,'max_witness_translation_m':.01,'max_final_root_axis_tilt_deg':20,
                        'max_vertical_displacement_m':.02}}


def evaluate_robot_visibility(measured, before, after):
    measured['checks']['target_visible_before_after'] = before['visible_pixels'] > 0 and after['visible_pixels'] > 0
    measured['success'] = all(measured['checks'].values())
    return measured
