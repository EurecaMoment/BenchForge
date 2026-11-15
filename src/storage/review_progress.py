"""Pure transitions for three checked, timed stage-review rounds."""
from copy import deepcopy

POLICY_VERSION = 1
ROUNDS = 3
WAIT_SECONDS = 60
RELEASED = ('elapsed', 'continued')


def check_reason(row, now, *, retry=False):
    if row['policy_version'] != POLICY_VERSION or row['state'] in ('paused', 'needs_input', 'superseded'):
        return None
    progress = row['progress']
    if row['state'] == 'check_failed' and not (retry or progress.get('pending_check')):
        return None
    if progress.get('continue_requested'):
        return 'continue'
    if row['state'] in RELEASED and not progress.get('pending_check'):
        return None
    if not progress.get('rounds'):
        return 'initial'
    if progress.get('pending_check'):
        return 'repair'
    if row['state'] in ('waiting_user', 'check_failed') and now >= row['deadline']:
        return 'advance'
    if retry and row['state'] == 'check_failed':
        return 'repair'
    return None


def checked(row, reason, receipt, now):
    """A check never backdates a new wait, even after a long process outage."""
    progress = deepcopy(row['progress'])
    progress['pending_check'] = False
    progress['last_check'] = receipt
    rounds = progress.setdefault('rounds', [])
    deadline = row['deadline']
    if not receipt['passed']:
        progress['failure'] = receipt
        return 'check_failed', deadline, progress
    progress.pop('failure', None)
    if reason == 'continue':
        progress['continue_requested'] = False
        progress['user_continued'] = True
        return 'continued', deadline, progress
    if reason == 'advance':
        rounds[-1]['wait_completed_at'] = now
    if not rounds or (reason == 'advance' and len(rounds) < ROUNDS):
        deadline = now + WAIT_SECONDS
        rounds.append({'number': len(rounds) + 1, 'check': receipt,
                       'wait_started_at': now, 'deadline': deadline})
    elif reason == 'repair':
        # A validated repair refreshes evidence, not the user's waiting time.
        rounds[-1]['last_recheck'] = receipt
    complete = sum('wait_completed_at' in item for item in rounds)
    state = 'continued' if progress.get('user_continued') else 'elapsed' if complete == ROUNDS else 'waiting_user'
    return state, deadline, progress


def public_progress(row, now):
    progress = row['progress']
    rounds = progress.get('rounds', [])
    return {
        'policy_version': row['policy_version'], 'rounds_required': ROUNDS,
        'round_number': min(ROUNDS, len(rounds) or 1),
        'checks_completed': len(rounds),
        'waits_completed': sum('wait_completed_at' in item for item in rounds),
        'wait_seconds': WAIT_SECONDS, 'rounds': rounds,
        'check_pending': row['policy_version'] == POLICY_VERSION and bool(
            progress.get('pending_check') or not rounds and row['state'] not in RELEASED),
        'last_check': progress.get('last_check'), 'check_failure': progress.get('failure'),
        'remaining_seconds': max(0, round(row['deadline'] - now)),
    }
