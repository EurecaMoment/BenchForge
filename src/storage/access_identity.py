"""Host-owned workspace registration and revocable access identities."""

import os
from pathlib import Path
import uuid

from sqlalchemy import select, update
from database.access import AccessScope, identifier, validate_scope
from database.engine import workspace_id as legacy_workspace_id
from database.migrations import initialize_schema
from database.schema import workspaces, workspace_sessions, runs
from database.transactions import lock_entity


def _scope(row):
    return AccessScope(**{name: row[name] for name in AccessScope.__dataclass_fields__})


def _query(host_id, session_id):
    return select(workspaces, workspace_sessions.c.session_id).join(
        workspace_sessions, workspaces.c.workspace_id == workspace_sessions.c.workspace_id
    ).where(workspace_sessions.c.host_id == host_id, workspace_sessions.c.session_id == session_id)


def register_session_workspace(engine, *, host_id, session_id, owner_id, root_path,
                               workspace_id=None, parent_session_id=None, legacy_namespace=None):
    """Called only after Host authorization; never infer ownership from old files."""
    for key, value in (("host", host_id), ("session", session_id), ("owner", owner_id)):
        identifier(value, key)
    if parent_session_id is not None:
        identifier(parent_session_id, "parent session")
    root = Path(root_path).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("A Host-created absolute workspace directory is required")
    canonical = os.path.normcase(str(root.resolve(strict=True)))
    stable_id = identifier(workspace_id or "workspace-" + uuid.uuid4().hex, "workspace")
    if legacy_namespace is not None:
        identifier(legacy_namespace, "legacy namespace")
    initialize_schema(engine)
    with engine.begin() as connection:
        lock_entity(connection, "workspace-registration", host_id, session_id)
        old = connection.execute(_query(host_id, session_id)).mappings().first()
        if old:
            if old["root_path"] != canonical or old["owner_id"] != owner_id or (workspace_id and old["workspace_id"] != workspace_id):
                raise PermissionError("Session workspace identity is already bound")
            if old["state"] != "active":
                raise PermissionError("Session workspace is revoked")
            return _scope(old)
        if parent_session_id:
            parent = connection.execute(_query(host_id, parent_session_id)).mappings().first()
            if not parent or parent["owner_id"] != owner_id or parent["state"] != "active":
                raise PermissionError("Parent session is not authorized")
        connection.execute(workspaces.insert().values(workspace_id=stable_id, host_id=host_id,
            owner_id=owner_id, root_path=canonical, legacy_namespace=legacy_namespace))
        connection.execute(workspace_sessions.insert().values(host_id=host_id, session_id=session_id,
            workspace_id=stable_id, parent_session_id=parent_session_id))
        return _scope(connection.execute(_query(host_id, session_id)).mappings().one())


def resolve_access_scope(engine, *, host_id, session_id, owner_id, policy_epoch=None, root_generation=None):
    for value in (host_id, session_id, owner_id):
        identifier(value)
    with engine.connect() as connection:
        row = connection.execute(_query(host_id, session_id)).mappings().first()
    if (not row or row["owner_id"] != owner_id or row["state"] != "active"
            or policy_epoch is not None and row["policy_epoch"] != policy_epoch
            or root_generation is not None and row["root_generation"] != root_generation):
        raise PermissionError("Workspace access is unavailable or stale")
    return _scope(row)


def revoke_scope(engine, scope):
    """Invalidate new access; caller must also reclaim live processes/mounts."""
    with engine.begin() as connection:
        lock_entity(connection, "workspace-identity", scope.workspace_id)
        validate_scope(connection, scope)
        connection.execute(update(workspaces).where(workspaces.c.workspace_id == scope.workspace_id)
                           .values(state="revoked", policy_epoch=scope.policy_epoch + 1))


def bind_run_scope(engine, scope, run_id):
    """Bind a freshly created/verified run, never trust a run ID from a model."""
    identifier(run_id, "run")
    with engine.begin() as connection:
        validate_scope(connection, scope)
        row = connection.execute(select(runs).where(runs.c.id == run_id).with_for_update()).mappings().first()
        if not row:
            raise PermissionError("Run is unavailable")
        if row["workspace_id"]:
            if (row["workspace_id"], row["session_id"]) != (scope.workspace_id, scope.session_id):
                raise PermissionError("Run belongs to another session")
            return
        context = row["plan"].get("context") or {}
        if context.get("session_id") != scope.session_id or context.get("workspace_id") not in scope.ledger_namespaces:
            raise PermissionError("Run ownership evidence does not match the trusted scope")
        connection.execute(runs.update().where(runs.c.id == run_id).values(
            workspace_id=scope.workspace_id, session_id=scope.session_id))


def approve_legacy_namespace(engine, scope, previous_root):
    """Explicit administrative mapping after verifying historical session ownership."""
    namespace = legacy_workspace_id(previous_root)
    with engine.begin() as connection:
        lock_entity(connection, "workspace-identity", scope.workspace_id)
        validate_scope(connection, scope)
        if scope.legacy_namespace not in (None, namespace):
            raise ValueError("A different historical namespace is already registered")
        connection.execute(workspaces.update().where(workspaces.c.workspace_id == scope.workspace_id)
                           .values(legacy_namespace=namespace, policy_epoch=scope.policy_epoch + 1))
    return resolve_access_scope(engine, host_id=scope.host_id, session_id=scope.session_id, owner_id=scope.owner_id)


def workspace_namespace(engine, root_path, *, host_id=None, session_id=None):
    """Trusted UI/service lookup, not authorization for a model-supplied root.

    An unregistered legacy root retains its old namespace. Ambiguous host roots
    require explicit Host/session identity rather than picking a random owner.
    """
    canonical = os.path.normcase(str(Path(root_path).expanduser().resolve()))
    query = select(workspaces.c.workspace_id, workspace_sessions.c.host_id, workspace_sessions.c.session_id).join(workspace_sessions,
        workspace_sessions.c.workspace_id == workspaces.c.workspace_id).where(workspaces.c.root_path == canonical)
    with engine.connect() as connection:
        rows = connection.execute(query).mappings().all()
    if not rows:
        return legacy_workspace_id(root_path)
    matches = [row["workspace_id"] for row in rows
               if (host_id is None or row["host_id"] == identifier(host_id, "host"))
               and (session_id is None or row["session_id"] == identifier(session_id, "session"))]
    if not matches:
        raise PermissionError("Workspace is not registered to this session")
    if len(matches) > 1:
        raise PermissionError("Workspace path has multiple Host identities; supply an explicit scope")
    return matches[0]
