"""Fixed read-only business projections; no caller SQL or physical file paths."""

from sqlalchemy import select
from database.access import read_transaction
from database.schema import runs, work_items, events, operations, reviews, active_plans, native_jobs


# 按资源类型与分页参数查询当前作用域可见的记录，限制返回字段和条数。
def query_records(engine, scope, resource, *, limit=30, after=None, enforce_role=True):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Query limit must be 1..100")
    namespace = scope.ledger_namespaces
    run_scope = select(runs.c.id).where(runs.c.workspace_id == scope.workspace_id, runs.c.session_id == scope.session_id)
    definitions = {
        "runs": (runs, ("id", "state", "created_at"), runs.c.id,
                 [runs.c.workspace_id == scope.workspace_id, runs.c.session_id == scope.session_id]),
        "tasks": (work_items, ("id", "run_id", "task_id", "state", "attempt", "lease_expires_at", "ready_at"), work_items.c.id,
                  [work_items.c.run_id.in_(run_scope)]),
        "events": (events, ("id", "run_id", "work_item_id", "event", "at"), events.c.id,
                   [events.c.run_id.in_(run_scope)]),
        "operations": (operations, ("sequence", "id", "tool", "state"), operations.c.sequence,
                       [operations.c.workspace_id.in_(namespace), operations.c.session_id == scope.session_id]),
        "reviews": (reviews, ("id", "stage", "state", "started", "deadline", "revision"), reviews.c.id,
                    [reviews.c.workspace_id.in_(namespace), reviews.c.session == scope.session_id]),
        "plans": (active_plans, ("session_id", "version", "pending"), active_plans.c.workspace_id,
                  [active_plans.c.workspace_id.in_(namespace), active_plans.c.session == scope.session_id]),
        "native_jobs": (native_jobs, ("job_id", "request_id", "session_id"), native_jobs.c.job_id,
                        [native_jobs.c.workspace_id.in_(namespace), native_jobs.c.session_id == scope.session_id]),
    }
    if resource not in definitions:
        raise ValueError("Unknown read-only query resource")
    table, fields, cursor, clauses = definitions[resource]
    if after is not None:
        expected = int if resource in ("events", "operations", "reviews") else str
        if type(after) is not expected or isinstance(after, str) and len(after) > 200:
            raise ValueError("Invalid query cursor")
        clauses.append(cursor > after)
    query = select(*(table.c[name] for name in fields), cursor.label("_cursor")).where(*clauses).order_by(cursor).limit(limit + 1)
    with read_transaction(engine, scope, enforce_role=enforce_role) as connection:
        rows = connection.execute(query).mappings().all()
    result = [{key: value for key, value in row.items() if key != "_cursor"} for row in rows[:limit]]
    return {"resource": resource, "items": result,
            "next": rows[limit - 1]["_cursor"] if len(rows) > limit else None}
