"""Explicit attempt recovery from native job receipts, never device liveness guesses."""
import json

from storage.files import write_json


# 按恢复模式准备采集修订，保留旧尝试历史并处理退役协议迁移。
def prepare_retry(directory, unit, mode, *, retired_protocol=False):
    if retired_protocol:
        if mode == 'resume':
            raise ValueError('The retired protocol cannot resume; retry with auto or restart')
        revision = unit['revision'] + 1
        program = json.loads((directory / 'program.json').read_text())
        target = directory.with_name(f'revision_{revision}') / 'program.json'
        if target.exists() and json.loads(target.read_text()) != program:
            raise ValueError('The next revision already contains another scene program')
        write_json(target, program)
        result = {**unit, 'revision': revision, 'capture_generation': 0,
                  'capture_recovery_mode': 'restart',
                  'capture_attempt_history': [*unit.get('capture_attempt_history', []),
                      {'revision': unit['revision'], 'recovery_mode': 'protocol_migration'}]}
        result.pop('capture_token', None)
        return result
    generation = unit.get('capture_generation', 0)
    receipts = list((directory / 'isaac_sim' / f'attempt_{generation}' / 'jobs').glob('*/receipt.json'))
    values = [json.loads(file.read_text()) for file in receipts]
    failed = any(str(value.get('state', '')).lower() in
                 ('failed', 'interrupted', 'cancelled', 'canceled') for value in values)
    chosen = ('restart' if failed else 'resume') if mode == 'auto' else mode
    if chosen == 'resume' and failed:
        raise ValueError('The Isaac Sim job is terminal; an explicit new attempt is required')
    if chosen == 'restart':
        active = [value.get('id') or value.get('job_id') for value in values
                  if str(value.get('state', '')).lower() in ('queued', 'running')]
        if active:
            raise ValueError('Resume observation of retained Isaac Sim jobs before restarting: '
                             + ', '.join(str(identity) for identity in active))
        generation += 1
    return {**unit, 'capture_generation': generation, 'capture_recovery_mode': chosen,
            'capture_attempt_history': [*unit.get('capture_attempt_history', []),
                {'generation': unit.get('capture_generation', 0), 'recovery_mode': chosen,
                 'jobs': [value.get('id') or value.get('job_id') for value in values]}]}
