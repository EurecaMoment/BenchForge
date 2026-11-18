"""Version 5: additive identities, scoped reads and immutable asset metadata."""

from sqlalchemy import inspect, text
from sqlalchemy.dialects.postgresql import JSONB
from database.schema import metadata


def _constraint(connection, table, name, definition):
    present = connection.execute(text("SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass(:table) AND conname=:name"),
                                 {"table": table, "name": name}).first()
    if not present:
        # Constants owned by this migration, never request strings.
        connection.exec_driver_sql(f'ALTER TABLE "{table}" ADD CONSTRAINT "{name}" {definition} NOT VALID')


def upgrade(connection):
    for name, column in (("runs", "workspace_id VARCHAR"), ("runs", "session_id VARCHAR"),
                         ("reviews", "session_id VARCHAR GENERATED ALWAYS AS (session) STORED"),
                         ("active_plans", "session_id VARCHAR GENERATED ALWAYS AS (session) STORED")):
        connection.exec_driver_sql(f'ALTER TABLE "{name}" ADD COLUMN IF NOT EXISTS {column}')
    for table in metadata.sorted_tables:
        for column in table.c:
            if isinstance(column.type, JSONB):
                kind = next((c["type"] for c in inspect(connection).get_columns(table.name) if c["name"] == column.name), None)
                if not isinstance(kind, JSONB):
                    connection.exec_driver_sql(f'ALTER TABLE "{table.name}" ALTER COLUMN "{column.name}" TYPE JSONB USING "{column.name}"::jsonb')
        for index in table.indexes:
            index.create(connection, checkfirst=True)
    for table, name, definition in (
        ("runs", "fk_run_session", "FOREIGN KEY (workspace_id,session_id) REFERENCES workspace_sessions(workspace_id,session_id)"),
        ("runs", "ck_run_scope_pair", "CHECK ((workspace_id IS NULL) = (session_id IS NULL))"),
        ("work_items", "fk_work_item_run", "FOREIGN KEY (run_id) REFERENCES runs(id)"),
        ("events", "fk_event_run", "FOREIGN KEY (run_id) REFERENCES runs(id)"),
        ("events", "fk_event_work_item", "FOREIGN KEY (work_item_id) REFERENCES work_items(id)"),
        ("benchforge_request_keys", "fk_request_run", "FOREIGN KEY (run_id) REFERENCES runs(id)"),
        ("legacy_review_ids", "fk_legacy_review", "FOREIGN KEY (review_id) REFERENCES reviews(id)"),
        ("legacy_events", "fk_legacy_event", "FOREIGN KEY (event_id) REFERENCES events(id)"),
    ):
        _constraint(connection, table, name, definition)
    from database.access_policies import install_policies
    install_policies(connection)
    connection.exec_driver_sql("""CREATE OR REPLACE FUNCTION benchforge_immutable_asset_version()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'Published asset versions are immutable'; END; $$""")
    connection.exec_driver_sql("DROP TRIGGER IF EXISTS benchforge_asset_version_immutable ON asset_versions")
    connection.exec_driver_sql("CREATE TRIGGER benchforge_asset_version_immutable BEFORE UPDATE ON asset_versions "
                               "FOR EACH ROW EXECUTE FUNCTION benchforge_immutable_asset_version()")
