"""Real PostgreSQL authorization and migration checks; opt-in disposable schemas."""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
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
    # 初始化独立 PostgreSQL schema 与多个会话身份，供真实行级隔离验证。
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

    # 验证重复登记保持稳定身份，不同会话查询严格隔离。
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

    # 两个会话同时首次使用一个已选项目，检查身份复用、会话隔离以及旧版本约束升级。
    def test_shared_project_registration(self):
        from database.engine import dispose_engines
        from database.migrations import versions
        from database.schema import workspace_sessions
        project = self.root / "shared"
        project.mkdir()
        with self.engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE workspace_sessions ADD UNIQUE (workspace_id)")
            connection.execute(versions.delete().where(versions.c.version == 7))
        dispose_engines()
        self.engine = engine_for(self.url)

        def register(session):
            return register_session_workspace(self.engine, host_id="host", session_id=session,
                workspace_id="selected-project", owner_id="owner", root_path=project)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = pool.map(register, ["first", "second"])
        self.assertEqual(first, register("first"))
        self.assertEqual(first.workspace_id, second.workspace_id)
        self.assertEqual(workspace_namespace(self.engine, project), first.workspace_id)
        with self.engine.begin() as connection:
            ids = connection.execute(select(workspace_sessions.c.session_id).where(
                workspace_sessions.c.workspace_id == first.workspace_id)).scalars().all()
            self.assertCountEqual(ids, ["first", "second"])
            for scope in (first, second):
                connection.execute(runs.insert().values(id="run-" + scope.session_id, state="PENDING",
                    plan={}, workspace_id=scope.workspace_id, session_id=scope.session_id))
        self.assertEqual([i["id"] for i in query_records(self.engine, first, "runs", enforce_role=False)["items"]],
                         ["run-first"])
        with self.assertRaises(PermissionError):
            bind_run_scope(self.engine, second, "run-first")
        with self.assertRaises(PermissionError):
            register_session_workspace(self.engine, host_id="host", session_id="third",
                owner_id="other", workspace_id=first.workspace_id, root_path=project)

    # 验证撤销使旧作用域失效，连接池复用不保留上一事务身份。
    def test_revoke_invalidates_old_scope_and_pool_state_is_transaction_local(self):
        a, b = self.scopes
        with read_transaction(self.engine, a, enforce_role=False) as connection:
            self.assertEqual(connection.execute(text("SELECT current_setting('benchforge.session_id')")).scalar_one(), a.session_id)
        with read_transaction(self.engine, b, enforce_role=False) as connection:
            self.assertEqual(connection.execute(text("SELECT current_setting('benchforge.session_id')")).scalar_one(), b.session_id)
        revoke_scope(self.engine, a)
        with self.assertRaises(PermissionError):
            query_records(self.engine, a, "runs", enforce_role=False)

    # 验证可信存储无线程身份时仍使用登记命名空间，而不是错误的路径摘要。
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

    # 验证真实只读角色不能看到其他会话或执行写入。
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
