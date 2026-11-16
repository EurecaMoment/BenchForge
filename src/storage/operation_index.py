"""Session-indexed workspace operations; schema and legacy migration live here."""

import json
from pathlib import Path
import sqlite3


FIELDS = ("id", "tool", "state", "artifact", "error", "session_id")


def ensure_schema(db, workspace):
    root = Path(workspace).resolve()
    columns = {row[1] for row in db.execute("PRAGMA table_info(operations)")}
    if (
        "session_id" in columns
        and db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='operations_session'"
        ).fetchone()
        and not db.execute("SELECT 1 FROM operations WHERE session_id IS NULL LIMIT 1").fetchone()
    ):
        return
    # Concurrent CLI/Host callers must not race ALTER TABLE or partial backfills.
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute(
            "CREATE TABLE IF NOT EXISTS operations "
            "(id TEXT PRIMARY KEY, tool TEXT, state TEXT, artifact TEXT, error TEXT, session_id TEXT)"
        )
        if "session_id" not in {row[1] for row in db.execute("PRAGMA table_info(operations)")}:
            db.execute("ALTER TABLE operations ADD COLUMN session_id TEXT")
        for identity, artifact in db.execute(
            "SELECT id,artifact FROM operations WHERE session_id IS NULL"
        ).fetchall():
            session = ""
            try:
                file = (root / artifact / "request.json").resolve()
                if file.is_relative_to(root) and file.stat().st_size <= 2 * 1024 * 1024:
                    session = (
                        json.loads(file.read_text(encoding="utf-8"))
                        .get("context", {})
                        .get("session_id")
                        or ""
                    )
                if not isinstance(session, str):
                    session = ""
            except (OSError, ValueError, TypeError, AttributeError):
                pass
            db.execute("UPDATE operations SET session_id=? WHERE id=?", (session, identity))
        db.execute("CREATE INDEX IF NOT EXISTS operations_session ON operations(session_id)")


def query_operations(workspace, *, sessions=(), history=False, limit=30, tools=()):
    if type(limit) is not int or not 1 <= limit <= 201:
        raise ValueError("Operation query limit must be 1..201")
    if not isinstance(sessions, (list, tuple)) or any(
        not isinstance(s, str) or not s for s in sessions
    ):
        raise ValueError("Session scope must contain nonempty IDs")
    if not history and not sessions:
        return []
    file = Path(workspace).resolve() / "runs.sqlite"
    if not file.is_file():
        return []
    db = sqlite3.connect(file, timeout=30)
    try:
        ensure_schema(db, workspace)
        conditions, values = [], []
        if not history:
            conditions.append("session_id IN (" + ",".join("?" for _ in sessions) + ")")
            values.extend(sessions)
        if tools:
            conditions.append("tool IN (" + ",".join("?" for _ in tools) + ")")
            values.extend(tools)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        rows = db.execute(
            "SELECT "
            + ",".join(FIELDS)
            + " FROM operations"
            + where
            + " ORDER BY rowid DESC LIMIT ?",
            [*values, limit],
        )
        return [dict(zip(FIELDS, row)) for row in rows]
    finally:
        db.close()
