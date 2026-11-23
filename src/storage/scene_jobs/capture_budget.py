"""Limit native S2 captures for one session's frozen scene design."""
import hashlib
import json

from sqlalchemy import text

from database.transactions import lock_entity
from storage.files import safe_path


LIMIT = 10
RESERVATION_EVENT = 'benchforge.s2_capture_reserved'


class CaptureBudgetExhausted(ValueError):
    code = 'S2_CAPTURE_BUDGET_EXHAUSTED'

    def __init__(self, budget):
        self.budget = budget
        message = (
            f"Production S2 scene {budget['scene_id']} used {budget['used']} of {LIMIT} native "
            'capture attempts for this frozen design. Inspect captured revisions with '
            'benchforge_scene_evidence. Reuse an accepted revision or review retained native '
            'evidence. Scene acceptance still requires native physics and visual evidence. '
            'The limit covers S2 native rendering. S3 collection and evidence validation use their own capabilities.'
        )
        self.issues = [{'code': self.code, 'path': '/scene_id', 'message': message,
                        'severity': 'error', 'blocking': True, 'basis': 'native_capture_history',
                        'capture_budget': budget}]
        super().__init__(message)

    def failure(self):
        return {'stage': 'capture_budget', 'error': type(self).__name__, 'code': self.code,
                'detail': str(self), 'capture_budget': self.budget}


def scene_key(program, design):
    """Bind the S2 budget to stable scene/design IDs, not plan file metadata."""
    design = design or {}
    design_id = design.get('scene_design_id') or design.get('id')
    values = (program.get('scene_id'), design_id)
    return values if all(isinstance(value, str) and value.strip() for value in values) else None


def admitted_scene_key(record, program):
    if record.get('enforced') is not True:
        return None
    return scene_key(program, record.get('scene_brief') or record.get('geometry_exception'))


def scene_fingerprint(key):
    return hashlib.sha256(json.dumps(key, separators=(',', ':')).encode()).hexdigest()


def capture_budget(connection, root, workspace, session, key, *, reservation=None, retained=False):
    """Count distinct native capture tokens using durable reservations and old receipts."""
    if reservation is not None:
        lock_entity(connection, 's2-capture-budget', workspace, session, *key)
    scene, design = key
    rows = connection.execute(text("""
        WITH owned_tasks AS (
            SELECT w.id, w.run_id, w.state, w.unit, w.failure, w.result
            FROM runs r JOIN work_items w ON w.run_id = r.id
            WHERE (r.workspace_id = :workspace AND r.session_id = :session)
               OR (r.workspace_id IS NULL
                   AND r.plan->'context'->>'workspace_id' IS NOT DISTINCT FROM :workspace
                   AND r.plan->'context'->>'session_id' IS NOT DISTINCT FROM :session)
        ), receipts AS (
            SELECT w.id, w.run_id, w.state, w.unit, review,
                   review->'production_acceptance' AS receipt
            FROM owned_tasks w
            CROSS JOIN LATERAL (SELECT COALESCE(w.failure->'scene_review', w.result->'scene_review') AS review) v
        ), committed AS (
            SELECT w.id AS task_id, w.unit->'revision' AS revision, e.payload->'report'->>'token' AS token
            FROM owned_tasks w JOIN events e ON e.run_id = w.run_id AND e.work_item_id = w.id
            WHERE e.event = 'benchforge.capture_committed'
              AND e.payload->'report'->>'status' = 'captured'
              AND e.payload->'report'->>'token' = w.unit->>'capture_token'
              AND e.payload->'report'->'frames' > '0'::jsonb
              AND w.unit->'intent'->>'execution_mode' IS DISTINCT FROM 'exploration'
        ), evidence AS (
            SELECT w.id AS task_id, e.payload->'revision' AS revision, e.payload->>'token' AS token,
                   false AS accepted, EXISTS (SELECT 1 FROM committed c WHERE c.task_id = w.id
                       AND c.token = e.payload->>'token') AS captured, e.id AS event_id, false AS needs_admission
            FROM owned_tasks w JOIN events e ON e.run_id = w.run_id AND e.work_item_id = w.id
            WHERE e.event = :reservation_event
              AND e.payload->'scene_key'->>0 = :scene
              AND e.payload->'scene_key'->>1 = :design
            UNION ALL
            SELECT w.id AS task_id, w.unit->'revision' AS revision, receipt->>'capture_token' AS token,
                   w.state = 'SUCCEEDED' AND review->'passed' = 'true'::jsonb AS accepted,
                   true AS captured, NULL AS event_id, false AS needs_admission
            FROM receipts w
            CROSS JOIN LATERAL (SELECT COALESCE(NULLIF(receipt->'scene_brief', 'null'::jsonb),
                                              receipt->'geometry_exception') AS declaration) d
            WHERE receipt->'enforced' = 'true'::jsonb AND receipt->>'scene_id' = :scene
              AND w.unit->'intent'->>'execution_mode' IS DISTINCT FROM 'exploration'
              AND COALESCE(w.unit->'intent'->'capture_options'->'view_indices', 'null'::jsonb) <> '[]'::jsonb
              AND COALESCE(declaration->>'scene_design_id', declaration->>'id') = :design
            UNION ALL
            SELECT task_id, revision, token, false AS accepted, true AS captured, NULL AS event_id,
                   true AS needs_admission FROM committed
        )
        SELECT DISTINCT ON (token) task_id, revision, token, COALESCE(accepted, false) AS accepted,
               captured, event_id, needs_admission
        FROM evidence WHERE token IS NOT NULL
        ORDER BY token, needs_admission, accepted DESC NULLS LAST, captured DESC, event_id DESC NULLS LAST
    """), {'workspace': workspace, 'session': session, 'scene': scene, 'design': design,
             'reservation_event': RESERVATION_EVENT}).mappings().all()
    attempts = {}
    for row in rows:
        if row['needs_admission']:
            path = safe_path(root, f"{row['task_id']}/revision_{row['revision']}/scene_admission.json")
            try:
                admission = json.loads(path.read_text(encoding='utf-8'))
            except FileNotFoundError:
                continue
            if admitted_scene_key(admission, admission) != key:
                continue
        attempts[row['token']] = dict(row)
    if reservation is not None and reservation['token'] not in attempts and (retained or len(attempts) < LIMIT):
        attempts[reservation['token']] = {**reservation, 'accepted': False, 'captured': False, 'event_id': None}
    evidence = sorted(attempts.values(), key=lambda row: (row['accepted'], row['captured'],
                      row['event_id'] or 0, row['task_id']), reverse=True)
    return attempts, {'scene_id': scene, 'scene_design_id': design, 'scene_key': scene_fingerprint(key), 'limit': LIMIT,
                      'used': len(attempts), 'remaining': max(0, LIMIT - len(attempts)),
                      'evidence': [{k: row[k] for k in ('task_id', 'revision', 'token', 'accepted', 'captured')}
                                   for row in evidence[:LIMIT]]}
