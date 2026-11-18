"""Atomically assign each source group to one immutable dataset partition."""

from pathlib import Path

from sqlalchemy import select

from database.config import resolve_workspace_url
from database.engine import engine_for
from database.migrations import initialize_schema
from database.schema import dataset_groups
from database.transactions import insert_missing, transaction
from storage.access_identity import workspace_namespace


def claim_partition(workspace, group, split):
    root = Path(workspace).resolve()
    engine = engine_for(resolve_workspace_url(root))
    initialize_schema(engine)
    namespace = workspace_namespace(engine, root)
    with transaction(engine) as connection:
        values = {"workspace_id": namespace, "id": group, "split": split}
        insert_missing(connection, dataset_groups, values, ["workspace_id", "id"])
        row = connection.execute(select(dataset_groups.c.split).where(
            dataset_groups.c.workspace_id == namespace, dataset_groups.c.id == group).with_for_update()).first()
        if row[0] != split:
            raise ValueError(f"Scene group {group} already belongs to {row[0]}; derivatives cannot cross splits")
        return row[0]
