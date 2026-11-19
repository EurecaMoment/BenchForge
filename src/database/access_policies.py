"""Schema-owned RLS policies and explicit least-privilege reader grants."""

from sqlalchemy import text
from database.schema import metadata


# 生成读取事务级身份设置的 SQL 表达式，并把空字符串视为缺失。
def setting(name):
    return f"NULLIF(current_setting('benchforge.{name}', true), '')"


# 为各业务表构造工作区、会话及资源授权的行级可见性条件。
def policy_expressions():
    workspace, session, host = (setting(name) for name in ("workspace_id", "session_id", "host_id"))
    own = f"workspace_id = {workspace}"
    registered = (f"{own} AND host_id = {host} AND owner_id = {setting('owner_id')} "
                  f"AND root_generation::text = {setting('root_generation')} "
                  f"AND policy_epoch::text = {setting('policy_epoch')} AND state = 'active'")
    active = f"EXISTS (SELECT 1 FROM workspaces w WHERE w.workspace_id = {workspace})"
    ledger = f"workspace_id IN ({workspace}, {setting('legacy_namespace')})"
    grant = (f"(audience = 'public' OR grantee_workspace_id = {workspace} OR grantee_owner_id = {setting('owner_id')}) "
             "AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)")
    expressions = {
        "workspaces": registered,
        "workspace_sessions": f"{own} AND host_id = {host} AND session_id = {session}",
        "runs": f"{own} AND session_id = {session}",
        "pipeline_documents": f"{own} AND session_id = {session}",
        "work_items": "EXISTS (SELECT 1 FROM runs r WHERE r.id = work_items.run_id)",
        "events": "EXISTS (SELECT 1 FROM runs r WHERE r.id = events.run_id)",
        "benchforge_request_keys": "EXISTS (SELECT 1 FROM runs r WHERE r.id = benchforge_request_keys.run_id)",
        "operations": f"{ledger} AND session_id = {session}",
        "reviews": f"{ledger} AND session = {session}",
        "active_plans": f"{ledger} AND session = {session}",
        "native_jobs": f"{ledger} AND session_id = {session}",
        "dataset_groups": own,
        "dsh_session_documents": f"host_id = {host} AND session_id = {session}",
        "assets": own,
        "asset_grants": f"{own} OR ({grant})",
        "asset_versions": f"{own} OR EXISTS (SELECT 1 FROM asset_grants g WHERE g.asset_version_id = asset_versions.asset_version_id AND ({grant}))",
    }
    return {name: expr if name == "workspaces" else f"({active}) AND ({expr})" for name, expr in expressions.items()}


# 启用并强制行级隔离，保留服务所有者写入权限并限制公共读策略。
def install_policies(connection):
    """Owner service retains writes; an independent reader gets scoped SELECT."""
    expressions = policy_expressions()
    quote = connection.dialect.identifier_preparer.quote
    for name in metadata.tables:
        table = quote(name)
        owner = connection.execute(text("SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=to_regclass(:table)"),
                                   {"table": name}).scalar_one()
        connection.exec_driver_sql(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        connection.exec_driver_sql(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        connection.exec_driver_sql(f"DROP POLICY IF EXISTS benchforge_service ON {table}")
        connection.exec_driver_sql(f"CREATE POLICY benchforge_service ON {table} TO {quote(owner)} USING (true) WITH CHECK (true)")
        connection.exec_driver_sql(f"DROP POLICY IF EXISTS benchforge_reader ON {table}")
        connection.exec_driver_sql(f"CREATE POLICY benchforge_reader ON {table} FOR SELECT TO PUBLIC USING ({expressions.get(name, 'false')})")


# 校验现有专用角色的权限，再授予受行级策略限制的读取能力。
def grant_reader(connection, role):
    """Grant an existing isolated role; role creation/passwords stay deployment-owned."""
    from database.access import identifier
    identifier(role, "reader role")
    info = connection.execute(text("SELECT rolsuper,rolbypassrls,rolcreaterole,rolcreatedb,rolreplication FROM pg_roles WHERE rolname=:role"), {"role": role}).first()
    if info is None or any(info):
        raise PermissionError("Reader role must exist without administrative or BYPASSRLS privileges")
    schema = connection.execute(text("SELECT current_schema()")).scalar_one()
    unsafe = connection.execute(text("""SELECT has_schema_privilege(:role,current_schema(),'CREATE') OR EXISTS (
      SELECT 1 FROM pg_roles member
      WHERE (member.rolsuper OR member.rolbypassrls OR member.rolcreaterole OR member.rolcreatedb OR member.rolreplication)
      AND pg_has_role(:role,member.oid,'MEMBER')) OR EXISTS (
      SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname=current_schema() AND c.relkind IN ('r','p')
      AND (pg_has_role(:role,c.relowner,'MEMBER') OR has_table_privilege(:role,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE')))"""), {"role": role}).scalar_one()
    if unsafe:
        raise PermissionError("Reader role inherits ownership or write privileges")
    quote = connection.dialect.identifier_preparer.quote
    connection.exec_driver_sql(f"GRANT USAGE ON SCHEMA {quote(schema)} TO {quote(role)}")
    for name in policy_expressions():
        connection.exec_driver_sql(f"GRANT SELECT ON {quote(schema)}.{quote(name)} TO {quote(role)}")
