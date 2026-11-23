"""Read repair lineage from completed, session-owned scene jobs."""
from sqlalchemy import text


def repair_history(engine, task, acceptance):
    """只查询同一已授权会话、冻结计划与场景下的终态采集。"""
    brief = acceptance.get('scene_brief') or {}
    if not brief.get('id') or not acceptance.get('scene_id'):
        return []
    with engine.connect() as connection:
        return connection.execute(text("""
            SELECT w.id AS task_id, w.unit->'revision' AS revision,
                   review->'passed' AS passed,
                   jsonb_build_object('decision', review->'repair_progress'->'decision',
                                      'baseline', review->'repair_progress'->'baseline') AS progress,
                   receipt->>'capture_token' AS token
            FROM runs current_run
            JOIN runs r ON r.workspace_id = current_run.workspace_id AND r.session_id = current_run.session_id
            JOIN work_items w ON w.run_id = r.id
            CROSS JOIN LATERAL (SELECT COALESCE(w.failure->'scene_review', w.result->'scene_review') AS review) v
            CROSS JOIN LATERAL (SELECT review->'production_acceptance' AS receipt) a
            WHERE current_run.id = :run_id AND w.id <> :task_id
              AND w.state IN ('SUCCEEDED','FAILED_FINAL') AND r.state <> 'CANCELED'
              AND receipt->'enforced' = 'true'::jsonb AND receipt->>'capture_token' IS NOT NULL
              AND receipt->>'scene_id' = :scene AND receipt->'scene_brief'->>'id' = :design
            ORDER BY (SELECT max(e.id) FROM events e WHERE e.run_id = r.id AND e.work_item_id = w.id
                      AND e.event = 'benchforge.transition' AND e.payload->>'state' = w.state),
                     r.created_at, w.id
        """), dict(run_id=task['run_id'], task_id=task['id'], scene=acceptance['scene_id'],
                   design=brief['id'])).mappings().all()
