"""Atomic active-plan versions, scoped to one workspace and conversation."""

from sqlalchemy import select

from database.config import resolve_workspace_url
from database.engine import engine_for, workspace_id
from database.migrations import initialize_schema
from database.schema import active_plans
from database.transactions import insert_statement, lock_entity, transaction


def _engine(workspace):
    engine = engine_for(resolve_workspace_url(workspace))
    initialize_schema(engine)
    return engine


def _query(namespace, session):
    return select(active_plans).where(active_plans.c.workspace_id == namespace,
                                      active_plans.c.session == session)


def current(workspace, session):
    with _engine(workspace).connect() as connection:
        row = connection.execute(_query(workspace_id(workspace), session)).mappings().first()
        return dict(row) if row else None


def activate(workspace, session, plan, *, expected=None, pending=False, changes=()):
    namespace = workspace_id(workspace)
    with transaction(_engine(workspace)) as connection:
        lock_entity(connection, "active-plan", namespace, session)
        old = connection.execute(_query(namespace, session).with_for_update()).mappings().first()
        if expected and old and old["plan"] != expected:
            raise ValueError("The plan changed in another operation. Refresh before saving.")
        if old and old["pending"] and expected != old["plan"]:
            raise ValueError("Read and apply the user-edited plan before replacing it.")
        values = dict(workspace_id=namespace, session=session, plan=str(plan),
                      previous=old["plan"] if old else expected,
                      version=old["version"] + 1 if old else 1,
                      pending=bool(pending), changes=list(changes))
        connection.execute(insert_statement(connection, active_plans).values(**values)
                           .on_conflict_do_update(index_elements=["workspace_id", "session"],
                                                  set_=values))
        # Return this transaction's version, not a subsequent writer's value.
        return values
