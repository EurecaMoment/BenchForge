"""Trusted query scopes; this API is never constructed from model arguments."""

from contextlib import contextmanager
from dataclasses import dataclass
import re

from sqlalchemy import text


def identifier(value, label="identity"):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}", value):
        raise ValueError(f"Invalid {label}")
    return value


@dataclass(frozen=True)
class AccessScope:
    host_id: str
    session_id: str
    workspace_id: str
    owner_id: str
    root_generation: int
    policy_epoch: int
    legacy_namespace: str | None = None

    def __post_init__(self):
        for field in ("host_id", "session_id", "workspace_id", "owner_id"):
            identifier(getattr(self, field), field)
        if self.legacy_namespace is not None:
            identifier(self.legacy_namespace, "legacy namespace")
        if any(type(value) is not int or value < 1 for value in (self.root_generation, self.policy_epoch)):
            raise ValueError("Invalid scope generation")

    @property
    def ledger_namespaces(self):
        return tuple(dict.fromkeys([self.workspace_id, *([self.legacy_namespace] if self.legacy_namespace else [])]))


def assert_reader_role(connection):
    """A query connection must not own tables, bypass RLS, or write them."""
    unsafe = connection.execute(text("""
        SELECT r.rolsuper OR r.rolbypassrls OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication
        OR has_schema_privilege(current_user,current_schema(),'CREATE')
        OR EXISTS (SELECT 1 FROM pg_roles member
          WHERE (member.rolsuper OR member.rolbypassrls OR member.rolcreaterole OR member.rolcreatedb OR member.rolreplication)
          AND pg_has_role(current_user,member.oid,'MEMBER')) OR EXISTS (
          SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
          WHERE n.nspname=current_schema() AND c.relkind IN ('r','p')
          AND (pg_has_role(current_user,c.relowner,'MEMBER')
            OR has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE')))
        FROM pg_roles r WHERE r.rolname=current_user
    """)).scalar_one()
    if unsafe:
        raise PermissionError("Database query role must be a non-owner without write or administrative privileges")


def set_scope(connection, scope):
    if not isinstance(scope, AccessScope):
        raise TypeError("A Host-resolved AccessScope is required")
    for name in ("host_id", "session_id", "workspace_id", "owner_id", "root_generation", "policy_epoch", "legacy_namespace"):
        connection.execute(text("SELECT set_config(:name, :value, true)"),
                           {"name": "benchforge." + name, "value": str(getattr(scope, name) or "")})


def validate_scope(connection, scope):
    from sqlalchemy import select
    from database.schema import workspaces, workspace_sessions
    if not isinstance(scope, AccessScope):
        raise TypeError("A Host-resolved AccessScope is required")
    row = connection.execute(select(workspaces, workspace_sessions.c.session_id).join(
        workspace_sessions, workspaces.c.workspace_id == workspace_sessions.c.workspace_id
    ).where(workspace_sessions.c.host_id == scope.host_id,
            workspace_sessions.c.session_id == scope.session_id)).mappings().first()
    if (not row or row["state"] != "active"
            or any(row[name] != getattr(scope, name) for name in AccessScope.__dataclass_fields__)):
        raise PermissionError("Workspace access is unavailable or stale")
    return row


@contextmanager
def read_transaction(engine, scope, *, enforce_role=True):
    """A transaction-local scope is rolled back before the connection is reused.

    enforce_role=False is only for trusted internal callers/tests, never a model
    endpoint. RLS settings are not credentials: arbitrary SQL is not supported.
    """
    with engine.connect() as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '5000ms'")
            if enforce_role:
                assert_reader_role(connection)
            set_scope(connection, scope)
            validate_scope(connection, scope)
            yield connection
