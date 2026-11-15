"""Durable review ledger. Domain checks run outside SQLite write transactions."""
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from service_access.tool_names import qualify
from storage.review_progress import POLICY_VERSION, WAIT_SECONDS, RELEASED, checked, public_progress


@contextmanager
def _database(workspace):
    database = Path(workspace) / 'stage-reviews.sqlite'
    database.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(database, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN IMMEDIATE')
        db.execute('CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY, session TEXT, key TEXT, stage TEXT, artifact TEXT, started REAL, deadline REAL, state TEXT, instruction TEXT, UNIQUE(session,key,stage))')
        columns = {row[1] for row in db.execute('PRAGMA table_info(reviews)')}
        for name, declaration in (('policy_version', 'INTEGER NOT NULL DEFAULT 0'),
                                  ('revision', 'INTEGER NOT NULL DEFAULT 0'),
                                  ('progress', "TEXT NOT NULL DEFAULT '{}'")):
            if name not in columns:
                db.execute(f'ALTER TABLE reviews ADD COLUMN {name} {declaration}')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _row(db, session, review_id=None):
    if review_id is None:
        row = db.execute('SELECT * FROM reviews WHERE session=? ORDER BY id DESC LIMIT 1', (session,)).fetchone()
    else:
        row = db.execute('SELECT * FROM reviews WHERE session=? AND id=?', (session, review_id)).fetchone()
        if row is None:
            raise ValueError('Review does not belong to this conversation')
    if row is None:
        return None
    value = dict(row)
    value['progress'] = json.loads(value['progress'])
    return value


def _save(db, row):
    db.execute('UPDATE reviews SET artifact=?,deadline=?,state=?,instruction=?,policy_version=?,revision=revision+1,progress=? WHERE id=?',
               (row['artifact'], row['deadline'], row['state'], row['instruction'], row['policy_version'],
                json.dumps(row['progress'], ensure_ascii=False), row['id']))


def _public(row, clock):
    if row is None:
        return {'state': 'none', 'wait_seconds': WAIT_SECONDS, 'can_continue': False}
    result = {key: value for key, value in row.items() if key not in ('id', 'progress')}
    result.update(public_progress(row, clock), review_id=row['id'], server_time=clock,
                  can_continue=row['state'] in RELEASED and not row['progress'].get('pending_check'))
    if row['state'] in RELEASED and row['progress'].get('pending_check'):
        result['state'] = 'waiting_user'
    messages = {
        'needs_input': 'Apply the saved user instruction before acknowledging it. The existing rounds are retained.',
        'paused': 'The user paused this review. Keep production stopped until the user continues it.',
        'check_failed': 'A real artifact check failed. Repair the reported issue, then use stage_review(action=retry). Do not wait for a failed check to approve itself.',
        'waiting_user': f'Present this artifact. Self-check round {result["round_number"]}/3; {result["waits_completed"]}/3 waits completed. Use stage_review(action=wait) or let the host timer advance it. Repairs retain this review.',
        'elapsed': 'All three self-checks and waits passed. Continue this production; do not reopen or wait again.',
        'continued': 'The user continued this review and its artifact check passed. Continue this production.',
        'superseded': 'A newer review owns this conversation. Read stage_review(action=status) without the obsolete review_id.',
    }
    result['next'] = qualify(messages[result['state']])
    if row['policy_version'] == 0 and row['state'] in RELEASED:
        result['next'] = 'This historical review was already released. Continue without reopening it.'
    return result


def review(workspace, session, action='status', *, stage=None, key=None, artifact=None,
           review_id=None, instruction='', now=None):
    if not session or not re.fullmatch(r'[\w-]{1,160}', session):
        raise ValueError('A valid DSH session is required')
    if action not in ('status', 'open', 'continue', 'pause', 'instruction', 'acknowledge', 'retry'):
        raise ValueError('Unknown stage review action')
    if action == 'open' and (not isinstance(stage, str) or not stage or not key or not artifact):
        raise ValueError('A completed stage, stable key and viewable artifact are required')
    clock = time.time() if now is None else now
    with _database(workspace) as db:
        latest = _row(db, session)
        if action == 'open':
            if (latest and latest['state'] in ('paused', 'needs_input')
                    and (latest['key'], latest['stage']) != (key, stage)):
                raise ValueError('Resolve the paused review or saved user instruction before opening another review')
            db.execute('INSERT OR IGNORE INTO reviews(session,key,stage,artifact,started,deadline,state,instruction,policy_version,progress) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (session, key, stage, str(artifact), clock, clock, 'waiting_user', '', POLICY_VERSION,
                        json.dumps({'pending_check': True, 'rounds': []})))
            review_id = db.execute('SELECT id FROM reviews WHERE session=? AND key=? AND stage=?', (session, key, stage)).fetchone()[0]
        row = _row(db, session, review_id)
        if row is None:
            return _public(None, clock)
        # The runner may read released earlier stages; stale controls cannot
        # mutate them or replace the latest stage.
        historical = latest and latest['id'] > row['id']
        if historical and action not in ('status', 'open'):
            raise ValueError('This review is superseded; use the current stage review')
        if historical and (action == 'status' or row['state'] not in RELEASED):
            return _public({**row, 'state': 'superseded'}, clock)
        if row['policy_version'] == 0 and row['state'] not in RELEASED:
            # Old waits prove no self-checks. Preserve finished reviews, pauses
            # and instructions, without inventing a completed round.
            row.update(policy_version=POLICY_VERSION, deadline=clock,
                       progress={'pending_check': True, 'rounds': [], 'migrated_from': 'deadline-only',
                                 'paused_by_user': row['state'] == 'paused'})
            _save(db, row)
        if action == 'open' and not historical:
            row['artifact'] = str(artifact)
            if row['policy_version'] == POLICY_VERSION:
                row['progress']['pending_check'] = True
            _save(db, row)
        if action in ('continue', 'pause', 'instruction', 'acknowledge', 'retry'):
            if action == 'instruction' and not instruction.strip():
                raise ValueError('User instruction must not be empty')
            if action in ('pause', 'instruction') and row['state'] in RELEASED:
                raise ValueError('This stage review has already ended')
            if action == 'acknowledge' and row['state'] != 'needs_input':
                raise ValueError('No pending instruction to acknowledge')
            if action == 'continue' and row['state'] == 'needs_input':
                raise ValueError('Apply and acknowledge the saved user instruction before continuing')
            if action in ('pause', 'instruction'):
                row['state'] = 'paused' if action == 'pause' else 'needs_input'
                row['progress'].pop('continue_requested', None)
                if action == 'pause':
                    row['progress']['paused_by_user'] = True
                if action == 'instruction':
                    row['instruction'] = instruction.strip()
            elif action == 'acknowledge':
                row['state'] = 'paused' if row['progress'].get('paused_by_user') else 'waiting_user'
                row['progress']['pending_check'] = True
                row['progress']['acknowledged_instruction'] = {'text': row['instruction'], 'at': clock}
                row['instruction'] = ''
            elif action == 'continue':
                if row['policy_version'] == 0:
                    row['state'] = 'continued'
                else:
                    row['progress']['continue_requested'] = True
                    row['progress']['paused_by_user'] = False
                    if row['state'] == 'paused':
                        row['state'] = 'waiting_user'
            _save(db, row)
        return _public(_row(db, session, row['id']), clock)


def snapshot(workspace, session, review_id):
    """Private state for a checker; public readers never approve by wall clock."""
    with _database(workspace) as db:
        row = _row(db, session, review_id)
        latest = _row(db, session)
        if latest['id'] != row['id']:
            row['state'] = 'superseded'
        return row


def complete_check(workspace, expected, reason, receipt, *, now=None):
    clock = time.time() if now is None else now
    with _database(workspace) as db:
        current = _row(db, expected['session'], expected['id'])
        latest = _row(db, expected['session'])
        if latest['id'] != current['id']:
            return _public({**current, 'state': 'superseded'}, clock)
        if current['revision'] == expected['revision']:
            state, deadline, progress = checked(current, reason, receipt, clock)
            current.update(state=state, deadline=deadline, progress=progress)
            _save(db, current)
        return _public(_row(db, expected['session'], expected['id']), clock)
