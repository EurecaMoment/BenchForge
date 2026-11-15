"""Expose recorded episodes only when their files belong to service acceptance."""
import json
import math
from pathlib import Path


def native_episodes(directory, receipt):
    directory = Path(directory)
    evidence = json.loads((directory / 'capture/evidence.json').read_text(encoding='utf-8'))
    episodes = []
    for action in evidence.get('interaction', {}).get('action_results', []):
        if not isinstance(action, dict):
            continue
        recording = action.get('recording') or {}
        name = action.get('trajectory_file') or recording.get('timeline_file')
        if not isinstance(name, str) or Path(name).name != name:
            continue
        file = directory / 'capture' / name
        version = receipt.get('evidence_versions', {}).get('capture/' + name)
        # Older captures remain usable as static scenes, but cannot certify episodes.
        if version is None:
            continue
        if (not file.is_file() or file.resolve() != file or file.stat().st_size > 32 * 1024 * 1024
                or version != [file.stat().st_size, file.stat().st_mtime_ns]):
            raise ValueError('Service-accepted trajectory changed; recapture the episode')
        value = json.loads(file.read_text(encoding='utf-8'))
        samples = value.get('trajectory', value.get('frames', []))
        if len(samples) < 3 or len(samples) != action.get('sample_count'):
            raise ValueError('Native episode needs intermediate samples, not only endpoints')
        steps = [row.get('step') for row in samples]
        if any(type(step) is not int for step in steps) or any(a >= b for a, b in zip(steps, steps[1:])):
            raise ValueError('Native episode simulation steps must advance monotonically')
        dt = value.get('dt', value.get('physics_dt'))
        if not isinstance(dt, (int, float)) or not math.isfinite(dt) or dt <= 0:
            raise ValueError('Native episode must retain its simulation time step')
        if action.get('trajectory_file'):
            if value.get('id') != action.get('id') or value.get('action') != action.get('action'):
                raise ValueError('Native trajectory identity disagrees with the captured action')
            for row in samples:
                timestamp = row.get('timestamp_sim')
                if not isinstance(timestamp, (int, float)) or not math.isclose(timestamp, row['step'] * dt, abs_tol=1e-6):
                    raise ValueError('Native episode timestamps must match its simulation steps')
        elif (value.get('source') != 'rendered_simulation_steps' or value.get('interpolated_frames') != 0
              or steps[-1] != value.get('last_frame_step')):
            raise ValueError('Native observation recording must preserve rendered simulation frames')
        episodes.append({'id': action['id'], 'action': action['action'], 'object_id': action.get('object_id'),
                         'samples': len(samples), 'first_step': steps[0], 'last_step': steps[-1], 'dt': dt,
                         'succeeded': action.get('success') is True or action.get('trajectory_complete') is True,
                         'robot_id': action.get('robot_id') or (value.get('robot') or {}).get('actor_id'),
                         'actor_states': value.get('actor_states', []),
                         'path': str(file), 'version': version,
                         'scope': 'recorded native action/observation; camera motion is not robot locomotion'})
    return episodes
