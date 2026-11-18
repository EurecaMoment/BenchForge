"""Durable stage-review ledger owned by the shared PostgreSQL service."""

from contextlib import contextmanager
import json
from pathlib import Path
import re
import time

from sqlalchemy import select, text, update

from database.config import resolve_workspace_url
from database.engine import engine_for, workspace_id
from database.migrations import initialize_schema
from database.schema import reviews, legacy_review_ids
from database.transactions import insert_missing, lock_entity, transaction
from service_access.tool_names import qualify
from storage.review_progress import POLICY_VERSION, WAIT_SECONDS, RELEASED, checked, public_progress


def _engine(workspace):
    return engine_for(resolve_workspace_url(workspace))


@contextmanager
def _database(workspace, session):
    root = Path(workspace).resolve()
    engine = _engine(root)
    initialize_schema(engine)
    namespace = workspace_id(root)
    with transaction(engine) as connection:
        lock_entity(connection, "stage-review", namespace, session)
        yield connection, namespace


def _row(connection, namespace, session, review_id=None, *, lock=False):
    query = select(reviews).where(reviews.c.workspace_id == namespace, reviews.c.session == session)
    query = query.where(reviews.c.id == review_id) if review_id is not None else query.order_by(reviews.c.id.desc()).limit(1)
    if lock:
        query = query.with_for_update()
    row = connection.execute(query).mappings().first()
    if review_id is not None and row is None:
        alias = connection.execute(select(legacy_review_ids.c.review_id).where(
            legacy_review_ids.c.workspace_id == namespace,
            legacy_review_ids.c.session == session,
            legacy_review_ids.c.legacy_id == review_id)).scalar()
        if alias is not None:
            query = select(reviews).where(reviews.c.workspace_id == namespace,
                                         reviews.c.session == session, reviews.c.id == alias)
            row = connection.execute(query.with_for_update() if lock else query).mappings().first()
    if review_id is not None and row is None:
        raise ValueError("Review does not belong to this conversation")
    if row is None:
        return None
    value = dict(row)
    progress = value.get("progress")
    value["progress"] = json.loads(progress or "{}") if isinstance(progress, str) else (progress or {})
    return value


def read_current(workspace, session, review_id=None, *, now=None):
    """Read one committed snapshot without taking the review mutation lock."""
    if not isinstance(session, str) or not re.fullmatch(r"[\w-]{1,160}", session):
        raise ValueError("A valid DSH session is required")
    if review_id is not None and (type(review_id) is not int or review_id < 1):
        raise ValueError("review_id must be a positive integer")
    engine = _engine(workspace)
    initialize_schema(engine)
    namespace = workspace_id(workspace)
    options = {"isolation_level": "REPEATABLE READ"}
    with engine.connect().execution_options(**options) as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        latest = _row(connection, namespace, session)
        row = latest if review_id is None else _row(connection, namespace, session, review_id)
        if row and latest["id"] != row["id"]:
            row = {**row, "state": "superseded"}
        return _public(row, time.time() if now is None else now)


def _save(connection, row):
    connection.execute(update(reviews).where(reviews.c.id == row["id"]).values(
        artifact=row["artifact"], deadline=row["deadline"], state=row["state"],
        instruction=row["instruction"], policy_version=row["policy_version"],
        revision=reviews.c.revision + 1, progress=row["progress"]))


def _public(row, clock):
    if row is None:
        return {"state": "none", "wait_seconds": WAIT_SECONDS, "can_continue": False}
    result = {key: value for key, value in row.items() if key not in ("id", "progress", "workspace_id")}
    result.update(public_progress(row, clock), review_id=row["id"], server_time=clock,
                   can_continue=row["state"] in RELEASED and not row["progress"].get("pending_check"))
    if row["state"] in RELEASED and row["progress"].get("pending_check"):
        result["state"] = "waiting_user"
    messages = {
        "needs_input": "Apply the saved user instruction before acknowledging it. The existing rounds are retained.",
        "paused": "The user paused this review. Keep production stopped until the user continues it.",
        "check_failed": "A real artifact check failed. Repair the reported issue, then use stage_review(action=retry). Do not wait for a failed check to approve itself.",
        "waiting_user": f"Present this artifact. Self-check round {result['round_number']}/3; {result['waits_completed']}/3 waits completed. Use stage_review(action=wait) or let the host timer advance it. Repairs retain this review.",
        "elapsed": "All three self-checks and waits passed. Continue this production; do not reopen or wait again.",
        "continued": "The user continued this review and its artifact check passed. Continue this production.",
        "superseded": "A newer review owns this conversation. Read stage_review(action=status) without the obsolete review_id.",
    }
    result["next"] = qualify(messages[result["state"]])
    if row["policy_version"] == 0 and row["state"] in RELEASED:
        result["next"] = "This historical review was already released. Continue without reopening it."
    return result


def review(workspace, session, action="status", *, stage=None, key=None, artifact=None,
           review_id=None, instruction="", now=None):
    if not session or not re.fullmatch(r"[\w-]{1,160}", session):
        raise ValueError("A valid DSH session is required")
    if action not in ("status", "open", "continue", "pause", "instruction", "acknowledge", "retry"):
        raise ValueError("Unknown stage review action")
    if action == "open" and (not isinstance(stage, str) or not stage or not key or not artifact):
        raise ValueError("A completed stage, stable key and viewable artifact are required")
    clock = time.time() if now is None else now
    with _database(workspace, session) as (connection, namespace):
        latest = _row(connection, namespace, session)
        if action == "open":
            if latest and latest["state"] in ("paused", "needs_input") and (latest["key"], latest["stage"]) != (key, stage):
                raise ValueError("Resolve the paused review or saved user instruction before opening another review")
            values = dict(workspace_id=namespace, session=session, key=key, stage=stage,
                artifact=str(artifact), started=clock, deadline=clock, state="waiting_user", instruction="",
                policy_version=POLICY_VERSION, revision=0, progress={"pending_check": True, "rounds": []})
            insert_missing(connection, reviews, values, ["workspace_id", "session", "key", "stage"])
            row = connection.execute(select(reviews.c.id).where(reviews.c.workspace_id == namespace,
                reviews.c.session == session, reviews.c.key == key, reviews.c.stage == stage)).scalar_one()
            review_id = row
        row = _row(connection, namespace, session, review_id, lock=action != "status")
        if row is None:
            return _public(None, clock)
        historical = latest and latest["id"] > row["id"]
        if historical and action not in ("status", "open"):
            raise ValueError("This review is superseded; use the current stage review")
        if historical and (action == "status" or row["state"] not in RELEASED):
            return _public({**row, "state": "superseded"}, clock)
        if row["policy_version"] == 0 and row["state"] not in RELEASED:
            row.update(policy_version=POLICY_VERSION, deadline=clock,
                       progress={"pending_check": True, "rounds": [], "migrated_from": "deadline-only",
                                 "paused_by_user": row["state"] == "paused"})
            _save(connection, row)
        if action == "open" and not historical:
            row["artifact"] = str(artifact)
            if row["policy_version"] == POLICY_VERSION:
                row["progress"]["pending_check"] = True
            _save(connection, row)
        if action in ("continue", "pause", "instruction", "acknowledge", "retry"):
            if action == "instruction" and not instruction.strip():
                raise ValueError("User instruction must not be empty")
            if action in ("pause", "instruction") and row["state"] in RELEASED:
                raise ValueError("This stage review has already ended")
            if action == "acknowledge" and row["state"] != "needs_input":
                raise ValueError("No pending instruction to acknowledge")
            if action == "continue" and row["state"] == "needs_input":
                raise ValueError("Apply and acknowledge the saved user instruction before continuing")
            if action in ("pause", "instruction"):
                row["state"] = "paused" if action == "pause" else "needs_input"
                row["progress"].pop("continue_requested", None)
                if action == "pause":
                    row["progress"]["paused_by_user"] = True
                if action == "instruction":
                    row["instruction"] = instruction.strip()
            elif action == "acknowledge":
                row["state"] = "paused" if row["progress"].get("paused_by_user") else "waiting_user"
                row["progress"]["pending_check"] = True
                row["progress"]["acknowledged_instruction"] = {"text": row["instruction"], "at": clock}
                row["instruction"] = ""
            elif action == "continue":
                if row["policy_version"] == 0:
                    row["state"] = "continued"
                else:
                    row["progress"]["continue_requested"] = True
                    row["progress"]["paused_by_user"] = False
                    if row["state"] == "paused":
                        row["state"] = "waiting_user"
            _save(connection, row)
        return _public(_row(connection, namespace, session, row["id"]), clock)


def snapshot(workspace, session, review_id):
    with _database(workspace, session) as (connection, namespace):
        row = _row(connection, namespace, session, review_id, lock=True)
        latest = _row(connection, namespace, session)
        if latest["id"] != row["id"]:
            row["state"] = "superseded"
        return row


def complete_check(workspace, expected, reason, receipt, *, now=None):
    clock = time.time() if now is None else now
    with _database(workspace, expected["session"]) as (connection, namespace):
        current = _row(connection, namespace, expected["session"], expected["id"], lock=True)
        latest = _row(connection, namespace, expected["session"])
        if latest["id"] != current["id"]:
            return _public({**current, "state": "superseded"}, clock)
        if current["revision"] == expected["revision"]:
            state, deadline, progress = checked(current, reason, receipt, clock)
            current.update(state=state, deadline=deadline, progress=progress)
            _save(connection, current)
        return _public(_row(connection, namespace, expected["session"], expected["id"]), clock)
