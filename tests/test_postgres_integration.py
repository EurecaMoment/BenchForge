"""Run against a disposable PostgreSQL database, never the deployed database."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
from threading import Barrier
import unittest
import uuid

from sqlalchemy import create_engine, select, text

from database.config import database_context
from database.engine import dispose_engines, engine_for, workspace_id
from database.import_records import migrate_workspace
from database.migrations import initialize_schema, versions, SCHEMA_VERSION
from database.schema import operations, reviews, runs, work_items, events, request_keys
from database.transactions import lock_entity
from storage.dataset_partitions import claim_partition
from storage.plan_versions import activate
from storage.stage_reviews import complete_check, review, snapshot, read_current
from server.database_migration import import_workspace


# 为旧工作区构造指定表的 JSONL 导出，供数据库迁移集成测试。
def import_rows(root, table, rows):
    directory=root / "database-import"
    directory.mkdir(exist_ok=True)
    (directory / (table + ".jsonl")).write_text("".join(json.dumps(row)+"\n" for row in rows), encoding="utf8")


@unittest.skipUnless(os.environ.get("BENCHFORGE_TEST_POSTGRES_URL"), "Requires disposable PostgreSQL")
class PostgresIntegrationTests(unittest.TestCase):
    # 连接专用集成测试数据库并创建临时工作区，准备真实并发事务验证。
    def setUp(self):
        self.url = os.environ["BENCHFORGE_TEST_POSTGRES_URL"]
        self.engine = engine_for(self.url)
        initialize_schema(self.engine)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.scope = database_context(self.url)
        self.scope.__enter__()
        self.addCleanup(self.scope.__exit__, None, None, None)

    # 在本组集成测试结束后释放全部共享连接池。
    @classmethod
    def tearDownClass(cls):
        dispose_engines()

    # 验证并发计划激活原子递增版本，每个调用得到自身已提交结果。
    def test_plan_versions_are_atomic_and_return_own_committed_version(self):
        barrier = Barrier(8)
        # 同步并发起跑后在独立数据库上下文保存一个计划版本。
        def save(index):
            with database_context(self.url):
                barrier.wait()
                return activate(self.root, "session", f"plan-{index}.json")
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(save, range(8)))
        self.assertEqual(sorted(row["version"] for row in rows), list(range(1, 9)))
        self.assertEqual([row["plan"] for row in rows], [f"plan-{i}.json" for i in range(8)])

    # 验证持有某会话计划锁时，其他会话仍能及时激活计划。
    def test_unrelated_conversations_do_not_wait_for_plan_lock(self):
        with self.engine.begin() as connection:
            lock_entity(connection, "active-plan", workspace_id(self.root), "blocked")
            # 在独立线程数据库上下文激活另一会话计划，以检测锁作用范围。
            def other():
                with database_context(self.url):
                    return activate(self.root, "independent", "new.json")
            with ThreadPoolExecutor(max_workers=1) as pool:
                self.assertEqual(pool.submit(other).result(timeout=3)["version"], 1)

    # 验证并发声明同一数据组时只能形成一个不可变分区。
    def test_immutable_partition_claims_under_contention(self):
        barrier = Barrier(8)
        # 在同步屏障后竞争不同分区，将预期冲突转成空结果。
        def claim(index):
            with database_context(self.url):
                barrier.wait()
                try:
                    return claim_partition(self.root, "scene", "train" if index % 2 else "test")
                except ValueError:
                    return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            rows = list(pool.map(claim, range(8)))
        self.assertEqual(len(set(row for row in rows if row)), 1)
        self.assertEqual(rows.count(None), 4)

    # 验证旧检查回执不能覆盖用户随后提交的暂停。
    def test_old_check_cannot_override_a_user_pause(self):
        opened = review(self.root, "session", "open", stage="S1", key="plan", artifact="plan.json")
        previous = snapshot(self.root, "session", opened["review_id"])
        review(self.root, "session", "pause")
        result = complete_check(self.root, previous, None, {"passed": True})
        self.assertEqual(result["state"], "paused")

    # 验证只读评审快照不等待同会话写入者持有的实体锁。
    def test_reads_do_not_wait_for_same_conversation_writer(self):
        opened = review(self.root, "session", "open", stage="S1", key="plan", artifact="plan.json")
        with self.engine.begin() as connection:
            lock_entity(connection, "stage-review", workspace_id(self.root), "session")
            connection.execute(reviews.update().where(reviews.c.id == opened["review_id"]).values(state="paused"))
            # 从另一连接读取当前评审，用于检查读写锁隔离。
            def read():
                with database_context(self.url):
                    return read_current(self.root, "session")
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = [pool.submit(read) for _ in range(8)]
                self.assertTrue(all(f.result(timeout=3)["state"] == "waiting_user" for f in futures))
        self.assertEqual(read_current(self.root, "session")["state"], "paused")

    # 验证两个旧工作区的重号评审可重复导入且正确重映射。
    def test_import_two_workspaces_with_colliding_review_ids_is_repeatable(self):
        results = []
        for name in ("first", "second"):
            root = self.root / name
            root.mkdir()
            import_rows(root, 'reviews', [dict(id=1, session='session', key='plan', stage='S1', state='paused')])
            operation = root / "operations/old"
            operation.mkdir(parents=True)
            (operation / "request.json").write_text(json.dumps({"context": {"session_id": "owner"}}))
            import_rows(root, 'operations', [dict(id='old', artifact='operations/old')])
            result = import_workspace(root, self.url)
            results.append(result)
            repeated = migrate_workspace(root, self.url)
            self.assertEqual(repeated["reviews"], 0)
            self.assertEqual(repeated["operations"], 0)
            self.assertEqual(repeated["review_ids"], result["review_ids"])
            projection = read_current(root, 'session', 1)
            self.assertEqual(projection['review_id'], result['review_ids']['1'])
            self.assertEqual(projection['state'], 'paused')
            with self.engine.connect() as connection:
                owner = connection.execute(select(operations.c.session_id)
                                           .where(operations.c.workspace_id == workspace_id(root))).scalar_one()
                self.assertEqual(owner, "owner")
        self.assertNotEqual(results[0]["review_ids"]["1"], results[1]["review_ids"]["1"])
        next_review = review(self.root, "new", "open", stage="S1", key="new", artifact="new.json")
        self.assertGreater(next_review["review_id"], max(row["review_ids"]["1"] for row in results))

    # 验证迁移保留场景运行历史及幂等请求键。
    def test_scene_history_and_idempotency_keys_survive_import(self):
        run_id = 'run_' + uuid.uuid4().hex
        key = 'key_' + uuid.uuid4().hex
        import_rows(self.root, 'runs', [dict(id=run_id, state='PENDING', plan={'workflow':'scene'})])
        import_rows(self.root, 'work_items', [dict(id=run_id+'.scene0', run_id=run_id, task_id='scene0', state='READY', unit={})])
        import_rows(self.root, 'events', [dict(id=1, run_id=run_id, payload={'saved':True})])
        import_rows(self.root, 'benchforge_request_keys', [dict(id=key, run_id=run_id)])
        first = migrate_workspace(self.root, self.url)
        repeated = migrate_workspace(self.root, self.url)
        for field in ('runs', 'work_items', 'events', 'request_keys'):
            self.assertEqual(first[field], 1)
            self.assertEqual(repeated[field], 0)
        with self.engine.connect() as connection:
            self.assertEqual(connection.execute(select(request_keys.c.run_id)
                                               .where(request_keys.c.id == key)).scalar_one(), run_id)
            self.assertEqual(connection.execute(select(events.c.payload)
                                               .where(events.c.run_id == run_id)).scalar_one(), {'saved': True})

    # 验证并发结算不会把已取消运行恢复为活动或成功状态。
    def test_scene_settlement_does_not_restore_a_canceled_run(self):
        from storage.scene_jobs.store import Store
        from types import SimpleNamespace
        root = self.root / 'scene-output'
        lifecycle = SimpleNamespace(admit=lambda *args: None, submission_identity=lambda value: value)
        store = Store(root, database_url=self.url, lifecycle=lifecycle)
        rid = store.submit('key_' + uuid.uuid4().hex, [{'name': 'test'}])
        with self.engine.begin() as connection:
            connection.execute(work_items.update().where(work_items.c.run_id == rid).values(state='RUNNING'))
        store.cancel(rid)
        store.settle_runs()
        self.assertEqual(store.snapshot(rid)['state'], 'CANCELED')

    # 验证提交不同请求键不会等待无关请求的事务锁。
    def test_submitting_another_request_does_not_wait_for_unrelated_key(self):
        from storage.scene_jobs.store import Store
        from types import SimpleNamespace
        lifecycle = SimpleNamespace(admit=lambda *args: None, submission_identity=lambda value: value)
        store = Store(self.root, database_url=self.url, lifecycle=lifecycle)
        with self.engine.begin() as connection:
            lock_entity(connection, 'scene-request', 'blocked-request')
            with ThreadPoolExecutor(max_workers=1) as pool:
                rid = pool.submit(store.submit, 'key_' + uuid.uuid4().hex, [{'name': 'test'}]).result(timeout=3)
            self.assertTrue(rid.startswith('sf_'))

    # 验证多个连接下结构升级安全执行，并覆盖旧版本升级路径。
    def test_schema_upgrade_is_safe_across_connections(self):
        # A separate schema isolates DDL and verifies the v1 -> v2 path.
        schema = "test_" + uuid.uuid4().hex
        with self.engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        engines = [create_engine(self.url, connect_args={"options": f"-c search_path={schema}"}) for _ in range(4)]
        try:
            initialize_schema(engines[0])
            with engines[0].begin() as connection:
                connection.execute(versions.delete().where(versions.c.version >= 2))
                connection.execute(text("DROP INDEX ix_operations_workspace_session_sequence"))
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(initialize_schema, engines[1:]))
            with engines[1].connect() as connection:
                self.assertEqual(connection.execute(select(versions.c.version)
                                                    .order_by(versions.c.version)).scalars().all(), list(range(1, SCHEMA_VERSION + 1)))
                self.assertIsNotNone(connection.execute(text("SELECT to_regclass('ix_operations_workspace_session_sequence')")).scalar())
        finally:
            for engine in engines:
                engine.dispose()
            with self.engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))


if __name__ == "__main__":
    unittest.main()
