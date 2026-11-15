"""Bind interactive answers to recorded service episodes, not a boolean claim."""
import math


def validate_native_episode(record, receipt, *, required=False, interaction=False, robot_locomotion=False):
    identifier = (record.get('provenance') or {}).get('native_episode_id')
    if not required and not identifier:
        return
    episode = next((row for row in receipt.get('native_episodes', []) if row['id'] == identifier), None)
    if not episode or not episode['succeeded'] or episode['samples'] < 3:
        raise ValueError('Native episode requires provenance.native_episode_id bound to a service-accepted recording with intermediate states; endpoint resets and interpolated paths are not execution evidence')
    if interaction and episode['action'] == 'record_trajectory':
        raise ValueError('Camera recording alone does not establish an executed object or robot interaction')
    if robot_locomotion:
        actor = (record.get('provenance') or {}).get('robot_actor_id')
        samples = episode.get('actor_states') or []
        if (episode['action'] != 'robot_locomotion' or not actor or episode.get('robot_id') != actor
                or len(samples) != episode['samples'] or len(samples) < 3
                or any(not isinstance(row, dict) or row.get('actor_id') != actor
                       or type(row.get('step')) is not int
                       or not isinstance(row.get('position'), list) or len(row['position']) != 3
                       or not isinstance(row.get('orientation_wxyz'), list) or len(row['orientation_wxyz']) != 4
                       or any(type(value) not in (int, float) or not math.isfinite(value)
                              for value in row.get('position', []) + row.get('orientation_wxyz', []))
                       for row in samples)):
            raise ValueError('Robot exploration needs an accepted robot_locomotion episode with native actor states; camera animation or Franka object-push is not robot locomotion')
        if (samples[0]['step'] != episode['first_step'] or samples[-1]['step'] != episode['last_step']
                or any(left['step'] >= right['step'] for left, right in zip(samples, samples[1:]))):
            raise ValueError('Robot actor state steps must increase across the accepted episode')
    return episode
