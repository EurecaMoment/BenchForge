"""Real PostgreSQL authorization and migration checks; opt-in disposable schemas."""

from pathlib import Path
import tempfile
import unittest
import uuid

from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from tests.database_fixtures import use_postgres
from database.access import read_transaction, set_scope
from database.access_policies import grant_reader
from database.engine import engine_for
from database.schema import runs, operations, workspaces
from storage.access_identity import register_session_workspace, revoke_scope, bind_run_scope, workspace_namespace
from storage.access_queries import query_records


class AccessPostgresTests(unittest.TestCase):
    def setUp(self):
        self.url = use_postgres(self)
        self.engine = engine_for(self.url)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.scopes = []
        for name in ("a", "b"):
            root = self.root / name
            root.mkdir()
            self.scopes.append(register_session_workspace(self.engine, host_id="host", session_id="session-" + name,
                owner_id="owner", root_path=root))

    def test_stable_registration_and_cross_session_reads(self):
        a, b = self.scopes
        same = register_session_workspace(self.engine, host_id="host", session_id="session-a",
            owner_id="owner", root_path=self.root / "a")
        self.assertEqual(a, same)
        with self.assertRaises(PermissionError):
            register_session_workspace(self.engine, host_id="host", session_id="session-a",
                owner_id="intruder", root_path=self.root / "a")
        for scope in self.scopes:
            rid = "run-" + scope.session_id
            with self.engine.begin() as connection:
                connection.execute(runs.insert().values(id=rid, state="PENDING", plan={"context": {
                    "workspace_id": scope.workspace_id, "session_id": scope.session_id}}))
            bind_run_scope(self.engine, scope, rid)
        result = query_records(self.engine, a, "runs", enforce_role=False)
        self.assertEqual([row["id"] for row in result["items"]], ["run-session-a"])
        with self.assertRaises(PermissionError):
            bind_run_scope(self.engine, b, "run-session-a")
        with self.assertRaises(PermissionError):
            query_records(self.engine, a, "runs")  # owner connections are not reader credentials

    def test_revoke_invalidates_old_scope_and_pool_state_is_transaction_local(self):
        a, b = self.scopes
        with read_transaction(self.engine, a, enforce_role=False) as connection:
            self.assertEqual(connection.execute(text("SELECT current_setting('benchforge.session_id')")).scalar_one(), a.session_id)
        with read_transaction(self.engine, b, enforce_role=False) as connection:
            self.assertEqual(connection.execute(text("SELECT current_setting('benchforge.session_id')")).scalar_one(), b.session_id)
        revoke_scope(self.engine, a)
        with self.assertRaises(PermissionError):
            query_records(self.engine, a, "runs", enforce_role=False)

    def test_existing_storage_uses_registered_namespace_without_thread_context(self):
        from storage.operation_index import query_operations
        from storage.plan_versions import activate, current
        from storage.stage_reviews import review, read_current
        from storage.dataset_partitions import claim_partition
        from storage.native_jobs import NativeJobs
        from types import SimpleNamespace
        a = self.scopes[0]
        root = self.root / "a"
        self.assertEqual(workspace_namespace(self.engine, root), a.workspace_id)
        with self.assertRaises(PermissionError):
            workspace_namespace(self.engine, root, session_id="session-b")
        with self.engine.begin() as connection:
            connection.execute(operations.insert().values(workspace_id=a.workspace_id,
                session_id=a.session_id, id="op-stable", state="completed"))
        self.assertEqual(query_operations(root, sessions=[a.session_id])[0]["id"], "op-stable")
        activate(root, a.session_id, "tasks/plan.json")
        self.assertEqual(current(root, a.session_id)["workspace_id"], a.workspace_id)
        review(root, a.session_id, "open", stage="S1", key="plan", artifact="tasks/plan.json")
        self.assertEqual(read_current(root, a.session_id)["state"], "waiting_user")
        claim_partition(root, "scene", "train")
        jobs = NativeJobs(root)
        self.assertEqual(jobs.workspace_id, a.workspace_id)
        jobs.remember("isaac", SimpleNamespace(job_id="job", request_id="request"), a.session_id)
        self.assertEqual(jobs.require("isaac", "job", a.session_id), "request")

    def test_rls_reader_cannot_see_other_session_or_write(self):
        with self.engine.connect() as connection:
            can_create = connection.execute(text("SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname=current_user")).scalar_one()
        if not can_create:
            self.skipTest("Disposable test role needs CREATEROLE to verify reader grants")
        role = "bf_reader_" + uuid.uuid4().hex
        with self.engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE ROLE "{role}" NOLOGIN NOSUPERUSER NOBYPASSRLS')
            grant_reader(connection, role)
            for scope in self.scopes:
                connection.execute(operations.insert().values(workspace_id=scope.workspace_id,
                    session_id=scope.session_id, id="op-" + scope.session_id, state="completed"))
        try:
            with self.engine.begin() as connection:
                connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                set_scope(connection, self.scopes[0])
                rows = connection.execute(select(operations.c.session_id)).scalars().all()
                self.assertEqual(rows, ["session-a"])
                self.assertEqual(connection.execute(select(workspaces.c.workspace_id)).scalars().all(), [self.scopes[0].workspace_id])
                from database.access import assert_reader_role
                assert_reader_role(connection)
            with self.assertRaises(DBAPIError):
                with self.engine.begin() as connection:
                    connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                    set_scope(connection, self.scopes[0])
                    connection.execute(operations.insert().values(workspace_id=self.scopes[0].workspace_id,
                        session_id="session-a", id="forbidden-write"))
            with self.engine.begin() as connection:
                connection.exec_driver_sql(f'SET LOCAL ROLE "{role}"')
                self.assertEqual(connection.execute(select(operations)).all(), [])
        finally:
            with self.engine.begin() as connection:
                connection.exec_driver_sql(f'DROP OWNED BY "{role}"')
                connection.exec_driver_sql(f'DROP ROLE "{role}"')


if __name__ == "__main__":
    unittest.main()
