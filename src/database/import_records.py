"""Explicit, read-only import of local ledgers into PostgreSQL.

No import runs on a request path. One workspace is committed atomically; repeat
imports keep the target's newer state. Large artifacts remain on the filesystem.
"""

import json
from pathlib import Path

from sqlalchemy import select

from database.config import normalize_database_url
from database.engine import engine_for, workspace_id
from database.migrations import initialize_schema
from database.schema import (active_plans, dataset_groups, native_jobs, operations, reviews,
                             legacy_review_ids, legacy_events, runs, work_items, events, request_keys)
from database.transactions import insert_statement, lock_entity


def _read(root, table):
    file = root / "database-import" / (table + ".jsonl")
    if not file.is_file():
        return
    with file.open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("Import rows must be JSON objects")
                yield row


def _json(value, default):
    return json.loads(value) if isinstance(value, str) and value else value or default


def _session(root, row):
    if row.get("session_id") is not None:
        return row["session_id"]
    directory = (root / (row.get("artifact") or "")).resolve()
    request = directory / "request.json"
    if not directory.is_relative_to(root):
        return ""
    try:
        if request.stat().st_size <= 2 * 1024 * 1024:
            return json.loads(request.read_text(encoding="utf-8")).get("context", {}).get("session_id") or ""
    except (OSError, ValueError, AttributeError):
        pass
    return ""


def _import(connection, table, values, keys):
    result = connection.execute(insert_statement(connection, table).values(**values)
                                .on_conflict_do_nothing(index_elements=keys)
                                .returning(table.c[keys[-1]]))
    return result.first() is not None


def _scene_history(connection, root, namespace):
    counts = dict(runs=0, work_items=0, events=0, request_keys=0)
    json_fields = {"runs": {"plan"}, "work_items": {"unit", "result", "artifact", "failure"}}
    for table, label in ((runs, "runs"), (work_items, "work_items"), (request_keys, "request_keys")):
        for row in _read(root, table.name):
            values = {key: value for key, value in row.items() if key in table.c}
            for key in json_fields.get(table.name, set()):
                if isinstance(values.get(key), str):
                    values[key] = json.loads(values[key])
            existing = connection.execute(select(table).where(table.c.id == values["id"])).mappings().first()
            if existing:
                fields = {"runs": ("plan",), "work_items": ("run_id", "task_id"),
                          "request_keys": ("run_id",)}[label]
                if any(existing[field] != values.get(field) for field in fields):
                    raise ValueError(f"Legacy {label} identity conflicts with the target database")
                continue
            counts[label] += _import(connection, table, values, ["id"])
    for row in _read(root, "events"):
        imported = connection.execute(select(legacy_events.c.event_id).where(
            legacy_events.c.workspace_id == namespace, legacy_events.c.legacy_id == row["id"])).first()
        if imported:
            continue
        values = {key: value for key, value in row.items() if key in events.c and key != "id"}
        if isinstance(values.get("payload"), str):
            values["payload"] = json.loads(values["payload"])
        event_id = connection.execute(events.insert().values(**values).returning(events.c.id)).scalar_one()
        connection.execute(legacy_events.insert().values(workspace_id=namespace,
                           legacy_id=row["id"], event_id=event_id))
        counts["events"] += 1
    return counts


def migrate_workspace(workspace, database_url):
    """Import a stopped workspace; report counts and remapped legacy review IDs."""
    root = Path(workspace).resolve(strict=True)
    target = engine_for(normalize_database_url(database_url))
    initialize_schema(target)
    namespace = workspace_id(root)
    imported = dict(operations=0, reviews=0, plans=0, groups=0, native_jobs=0, review_ids={})
    sessions = set()
    with target.begin() as connection:
        lock_entity(connection, "legacy-import", namespace)
        imported.update(_scene_history(connection, root, namespace))
        for row in _read(root, "operations"):
            values = {key: row.get(key) for key in ("id", "tool", "state", "artifact", "error")}
            values.update(workspace_id=namespace, session_id=_session(root, row))
            imported["operations"] += _import(connection, operations, values, ["workspace_id", "id"])
        for row in _read(root, "reviews"):
            values = {key: row.get(key) for key in
                      ("session", "key", "stage", "artifact", "started", "deadline", "state")}
            values.update(workspace_id=namespace, instruction=row.get("instruction") or "",
                          started=row.get("started") or 0, deadline=row.get("deadline") or 0,
                          policy_version=row.get("policy_version") or 0,
                          revision=row.get("revision") or 0, progress=_json(row.get("progress"), {}))
            keys = ["workspace_id", "session", "key", "stage"]
            # Local integer IDs collide across workspaces. PostgreSQL allocates
            # the identity; never reset its sequence while other writers run.
            imported["reviews"] += _import(connection, reviews, values, keys)
            new_id = connection.execute(select(reviews.c.id).where(
                *(reviews.c[key] == values[key] for key in keys))).scalar_one()
            imported["review_ids"][str(row["id"])] = new_id
            sessions.add(row["session"])
            _import(connection, legacy_review_ids,
                    dict(workspace_id=namespace, session=row["session"], legacy_id=row["id"], review_id=new_id),
                    ["workspace_id", "session", "legacy_id"])
            mapped = connection.execute(select(legacy_review_ids.c.review_id).where(
                legacy_review_ids.c.workspace_id == namespace,
                legacy_review_ids.c.session == row["session"],
                legacy_review_ids.c.legacy_id == row["id"])).scalar_one()
            if mapped != new_id:
                raise ValueError("Legacy review identity changed since the previous import")
        for row in _read(root, "active_plans"):
            values = {key: row.get(key) for key in ("session", "plan", "previous")}
            values.update(workspace_id=namespace, version=row.get("version") or 1,
                          pending=bool(row.get("pending")), changes=_json(row.get("changes"), []))
            imported["plans"] += _import(connection, active_plans, values, ["workspace_id", "session"])
        for row in _read(root, "dataset_groups"):
            values = dict(workspace_id=namespace, id=row["id"], split=row["split"])
            imported["groups"] += _import(connection, dataset_groups, values, ["workspace_id", "id"])
            split = connection.execute(select(dataset_groups.c.split).where(
                dataset_groups.c.workspace_id == namespace, dataset_groups.c.id == row["id"])).scalar_one()
            if split != row["split"]:
                raise ValueError("Legacy dataset partition conflicts with the target database")
        for row in _read(root, "native_jobs"):
            values = {key: row[key] for key in ("endpoint", "job_id", "session_id", "request_id")}
            values["workspace_id"] = namespace
            keys = ["workspace_id", "endpoint", "job_id"]
            imported["native_jobs"] += _import(connection, native_jobs, values, keys)
            owner = connection.execute(select(native_jobs.c.session_id, native_jobs.c.request_id)
                                       .where(*(native_jobs.c[key] == values[key] for key in keys))).one()
            if tuple(owner) != (row["session_id"], row["request_id"]):
                raise ValueError("Legacy native job ownership conflicts with the target database")
    return {**imported, "review_sessions": sorted(sessions)}
