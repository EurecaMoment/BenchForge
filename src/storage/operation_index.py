"""Workspace-scoped operation index backed by the shared database pool."""

from pathlib import Path

from sqlalchemy import select

from database.config import resolve_workspace_url
from database.engine import engine_for
from database.migrations import initialize_schema
from database.schema import operations
from storage.access_identity import workspace_namespace


FIELDS = ("id", "tool", "state", "artifact", "error", "session_id")


def _engine(workspace, database_url=None):
    return engine_for(resolve_workspace_url(workspace, database_url))


def query_operations(workspace, *, sessions=(), history=False, limit=30, tools=(), database_url=None):
    if type(limit) is not int or not 1 <= limit <= 201:
        raise ValueError("Operation query limit must be 1..201")
    if not isinstance(sessions, (list, tuple)) or any(not isinstance(s, str) or not s for s in sessions):
        raise ValueError("Session scope must contain nonempty IDs")
    if not history and not sessions:
        return []
    root = Path(workspace).resolve()
    engine = _engine(root, database_url)
    initialize_schema(engine)
    namespace = workspace_namespace(engine, root)
    with engine.begin() as connection:
        clauses = [operations.c.workspace_id == namespace]
        if not history:
            clauses.append(operations.c.session_id.in_(sessions))
        if tools:
            clauses.append(operations.c.tool.in_(tools))
        rows = connection.execute(select(operations.c.id, operations.c.tool, operations.c.state,
                                         operations.c.artifact, operations.c.error, operations.c.session_id)
                                   .where(*clauses)
                                   .order_by(operations.c.sequence.desc()).limit(limit)).mappings()
        return [dict(row) for row in rows]
