"""Version 6: personal asset libraries and authoritative pipeline documents."""

from database.schema import asset_grants, pipeline_documents


# 补齐资源来源、个人授权及文档隔离字段和相应约束。
def upgrade(connection):
    connection.exec_driver_sql("ALTER TABLE asset_versions ADD COLUMN IF NOT EXISTS provenance JSONB NOT NULL DEFAULT '{}'::jsonb")
    connection.exec_driver_sql("ALTER TABLE asset_grants ADD COLUMN IF NOT EXISTS grantee_owner_id VARCHAR")
    connection.exec_driver_sql("ALTER TABLE asset_grants DROP CONSTRAINT IF EXISTS ck_asset_grant_audience")
    connection.exec_driver_sql("""ALTER TABLE asset_grants ADD CONSTRAINT ck_asset_grant_audience CHECK (
       (audience='public' AND grantee_workspace_id IS NULL AND grantee_owner_id IS NULL) OR
       (grantee_workspace_id IS NOT NULL AND grantee_owner_id IS NULL AND audience='workspace:' || grantee_workspace_id) OR
       (grantee_owner_id IS NOT NULL AND grantee_workspace_id IS NULL AND audience='owner:' || grantee_owner_id))""")
    pipeline_documents.create(connection, checkfirst=True)
    for index in asset_grants.indexes:
        index.create(connection, checkfirst=True)
    from database.access_policies import install_policies
    install_policies(connection)
