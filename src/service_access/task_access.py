"""Recover a queued task's trusted conversation identity from service records."""

from sqlalchemy import select

from database.schema import runs, work_items, workspaces, workspace_sessions
from service_access.access_scope import TrustedAccess, current_access
from storage.access_identity import resolve_access_scope


# 从已存储运行与工作项恢复访问作用域，拒绝将不完整的 DSH 身份当作操作者任务。
def task_access(engine, task_id):
    """Restore scope without treating incomplete DSH ownership as operator work."""
    with engine.connect() as connection:
        row = connection.execute(select(runs, work_items.c.unit.label('task_unit')).join(
            work_items, work_items.c.run_id == runs.c.id
        ).where(work_items.c.id == task_id)).mappings().first()
        if not row:
            raise PermissionError('Task is unavailable')
        plan_context = row['plan'].get('context') or {}
        task_context = (row.get('task_unit') or {}).get('run_context') or {}
        contexts = (row, plan_context, task_context)
        workspace_ids = {value['workspace_id'] for value in contexts if value.get('workspace_id')}
        session_ids = {value['session_id'] for value in contexts if value.get('session_id')}
        if not workspace_ids and not session_ids:
            if current_access() is not None:
                raise PermissionError('A conversation cannot consume an unowned operator task')
            return None  # Explicit operator tasks retain their separate trusted path.
        if not workspace_ids or len(session_ids) != 1:
            raise PermissionError('Task has incomplete or conflicting conversation ownership')
        workspace_id = next(value['workspace_id'] for value in contexts if value.get('workspace_id'))
        session_id = next(iter(session_ids))
        identity = connection.execute(select(workspaces, workspace_sessions.c.session_id).join(
            workspace_sessions, workspace_sessions.c.workspace_id == workspaces.c.workspace_id
        ).where(workspaces.c.workspace_id == workspace_id,
                workspace_sessions.c.session_id == session_id)).mappings().first()
    if not identity:
        raise PermissionError('Task has no registered conversation owner')
    scope = resolve_access_scope(engine, host_id=identity['host_id'], session_id=session_id,
                                 owner_id=identity['owner_id'])
    if not workspace_ids <= set(scope.ledger_namespaces):
        raise PermissionError('Task has conflicting conversation workspaces')
    active = current_access()
    if active is not None and active.scope != scope:
        raise PermissionError('Task belongs to another conversation')
    return TrustedAccess(scope, identity['root_path'])
