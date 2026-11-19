"""Persist feature operations in the selected workspace."""
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from storage.files import write_json as write
from storage.operation_index import query_operations
from database.config import configured_database_url, database_context, resolve_workspace_url
from database.engine import engine_for
from storage.access_identity import workspace_namespace
from database.migrations import initialize_schema
from database.schema import operations
from sqlalchemy import update
from storage.operation_outputs import operation_outputs, logical_value

class WorkspaceJobs:
    # 绑定工作区、配置、DSH 会话及共享数据库命名空间，准备操作存储。
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
        self.workspace_id = workspace_namespace(self.engine, self.root)

    # 保留兼容的关闭入口；连接池由进程统一管理，不属于单次请求。
    def close(self):
        """This request owns no pool; process shutdown releases shared engines."""

    # 核验宿主工作区身份并解析受限输入参数，在当前数据库上下文中调用操作。
    def call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        from service_access.input_policy import resolve_arguments
        from service_access.access_scope import current_access
        access = current_access()
        if access is not None:
            if self.root != Path(access.root_path).resolve():
                raise PermissionError('Operation workspace differs from its Host identity')
            self.workspace_id = access.scope.workspace_id
        args = resolve_arguments(args, self.root)
        with database_context(self.database_url):
            return self._call(tool, args)

    # 分配操作编号并管理私有输出上下文，将状态查询或执行失败写入操作账本。
    def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute one harness operation and persist its request and result."""
        if tool == "status":
            session = self.dsh_context.get('session_id')
            return {"operations": query_operations(self.root, sessions=[session] if session else [],
                                                   history=not bool(session), database_url=self.database_url)}
        operation = uuid.uuid4().hex[:16]
        from service_access.access_scope import current_access
        try:
            with operation_outputs(self.root, operation, scoped=current_access() is not None) as directory:
                return self._execute_operation(tool, args, operation, directory)
        except Exception as exc:
            with self.engine.begin() as connection:
                connection.execute(update(operations).where(
                    operations.c.workspace_id == self.workspace_id, operations.c.id == operation
                ).values(state='failed', error=str(exc)))
            raise

    # 持久化请求、调用实际工具、发布产物并更新操作结果及成功失败状态。
    def _execute_operation(self, tool, args, operation, directory):
        public = self.root / 'operations' / operation
        write(directory / "request.json", {"tool": tool, "arguments": args, "time": datetime.now(timezone.utc).isoformat(), "context": self.dsh_context})
        with self.engine.begin() as connection:
            connection.execute(operations.insert().values(
                workspace_id=self.workspace_id, id=operation, tool=tool, state="running",
                artifact=str(public), error=None, session_id=self.dsh_context.get('session_id', '')))
        try:
            value = self.execute(tool, {**args, '_dsh_context': self.dsh_context} if self.dsh_context else args, directory)
            write(directory / "result.json", value)
            from storage.operation_outputs import publish_current
            publish_current()
            with self.engine.begin() as connection:
                connection.execute(update(operations).where(
                    operations.c.workspace_id == self.workspace_id, operations.c.id == operation
                ).values(state='completed'))
            return logical_value({"operation_id": operation, "artifact_dir": str(public), **value})
        except Exception as exc:
            with self.engine.begin() as connection:
                connection.execute(update(operations).where(
                    operations.c.workspace_id == self.workspace_id, operations.c.id == operation
                ).values(state='failed', error=str(exc)))
            raise
