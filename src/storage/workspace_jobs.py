"""Persist feature operations in the selected workspace."""
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from storage.files import write_json as write
from storage.operation_index import query_operations
from database.config import configured_database_url, database_context, resolve_workspace_url
from database.engine import engine_for, workspace_id
from database.migrations import initialize_schema
from database.schema import operations
from sqlalchemy import update

class WorkspaceJobs:
    def __init__(self, workspace, config=None):
        self.root = Path(workspace).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.config = config or {}
        self.dsh_context = {'session_id': os.environ['BENCHFORGE_DSH_SESSION_ID'],
                            'call_id': os.environ.get('BENCHFORGE_DSH_CALL_ID', '')} if os.environ.get('BENCHFORGE_DSH_SESSION_ID') else {}
        self.database_url = resolve_workspace_url(
            self.root, configured_database_url(self.config))
        self.engine = engine_for(self.database_url)
        initialize_schema(self.engine)
        self.workspace_id = workspace_id(self.root)

    def close(self):
        """This request owns no pool; process shutdown releases shared engines."""

    def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        with database_context(self.database_url):
            return self._call(tool, args)

    def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute one harness operation and persist its request and result."""
        if tool == "status":
            session = self.dsh_context.get('session_id')
            return {"operations": query_operations(self.root, sessions=[session] if session else [],
                                                   history=not bool(session), database_url=self.database_url)}
        operation = uuid.uuid4().hex[:16]
        directory = self.root / "operations" / operation
        directory.mkdir(parents=True)
        write(directory / "request.json", {"tool": tool, "arguments": args, "time": datetime.now(timezone.utc).isoformat(), "context": self.dsh_context})
        with self.engine.begin() as connection:
            connection.execute(operations.insert().values(
                workspace_id=self.workspace_id, id=operation, tool=tool, state="running",
                artifact=str(directory), error=None, session_id=self.dsh_context.get('session_id', '')))
        try:
            value = self.execute(tool, {**args, '_dsh_context': self.dsh_context} if self.dsh_context else args, directory)
            write(directory / "result.json", value)
            with self.engine.begin() as connection:
                connection.execute(update(operations).where(
                    operations.c.workspace_id == self.workspace_id, operations.c.id == operation
                ).values(state='completed'))
            return {"operation_id": operation, "artifact_dir": str(directory), **value}
        except Exception as exc:
            with self.engine.begin() as connection:
                connection.execute(update(operations).where(
                    operations.c.workspace_id == self.workspace_id, operations.c.id == operation
                ).values(state='failed', error=str(exc)))
            raise
