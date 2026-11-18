"""Database selection, lifecycle and transaction regression contracts."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests.database_fixtures import use_postgres
from sqlalchemy import select

from database.config import (configured_database_url, database_context, normalize_database_url,
                             resolve_database_url, resolve_workspace_url)
from database.engine import dispose_engines, engine_for
from database.migrations import initialize_schema
from database.schema import operations
from storage.dataset_partitions import claim_partition
from storage.operation_index import query_operations
from storage.plan_versions import activate
from storage.stage_reviews import review, read_current
from storage.workspace_jobs import WorkspaceJobs
from server.operation_config import load_operation_config


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.addCleanup(dispose_engines)
        self.root = Path(directory.name)
        self.url = use_postgres(self)

    def test_runtime_requires_postgresql(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                resolve_workspace_url(self.root)
            with self.assertRaises(ValueError):
                engine_for()
        for value in ('file:///tmp/records', 'mysql://host/db', ''):
            with self.assertRaises(ValueError):
                normalize_database_url(value)

    def test_context_is_nested_and_explicit_configuration_wins(self):
        with patch.dict(os.environ, {"BENCHFORGE_DATABASE_URL": "postgres://env/db"}):
            with database_context("postgresql://outer/db"):
                self.assertEqual(resolve_workspace_url(self.root), "postgresql+psycopg://outer/db")
                with database_context("postgresql://inner/db"):
                    self.assertEqual(resolve_database_url(), "postgresql+psycopg://inner/db")
                self.assertEqual(resolve_database_url(), "postgresql+psycopg://outer/db")
                self.assertEqual(resolve_database_url(database_url="postgres://explicit/db"),
                                 "postgresql+psycopg://explicit/db")
            self.assertEqual(resolve_database_url(), "postgresql+psycopg://env/db")
        with self.assertRaisesRegex(ValueError, "unset environment"):
            configured_database_url({"database_url_env": "BENCHFORGE_MISSING_DATABASE_FOR_TEST"})

    def test_one_runtime_uses_its_config_for_all_nested_stores(self):
        class Runtime(WorkspaceJobs):
            def execute(self, tool, args, directory):
                claim_partition(self.root, "scene", "train")
                activate(self.root, "session-a", "plan.json")
                return {"ok": True}
        with patch.dict(os.environ, {"BENCHFORGE_DATABASE_URL": "postgresql://unused/db"}):
            runtime = Runtime(self.root, {"database_url": self.url})
            with patch.object(runtime.engine, "dispose") as dispose:
                runtime.call("test", {})
                runtime.close()
                dispose.assert_not_called()
            with database_context(self.url):
                self.assertEqual(len(query_operations(self.root, history=True)), 1)

    def test_thread_contexts_do_not_leak(self):
        def resolve(index):
            url = f"postgres://host{index}/database"
            with database_context(url):
                return resolve_workspace_url(self.root)
        with ThreadPoolExecutor(max_workers=8) as pool:
            result = list(pool.map(resolve, range(16)))
        self.assertEqual(result, [f"postgresql+psycopg://host{i}/database" for i in range(16)])

    def test_provider_tools_read_database_from_referenced_service_config(self):
        config = self.root / 'service.json'
        config.write_text(json.dumps({'execution_mode': 'exploration', 'database_url': self.url}))
        providers = self.root / 'models.json'
        providers.write_text(json.dumps({'providers': {'FastQwen': {'model': 'qwen'}}}))
        with patch.dict(os.environ, {'BENCHFORGE_SERVICE_CONFIG': str(config)}):
            result = load_operation_config(providers)
        self.assertEqual(result['database_url'], self.url)
        self.assertEqual(result['providers']['FastQwen']['model'], 'qwen')

    def test_failed_transaction_rolls_back_and_engine_is_reusable(self):
        engine = engine_for(self.url)
        initialize_schema(engine)
        with self.assertRaises(RuntimeError):
            with engine.begin() as connection:
                connection.execute(operations.insert().values(workspace_id="a", id="b"))
                raise RuntimeError("abort")
        with engine.connect() as connection:
            self.assertEqual(connection.execute(select(operations)).all(), [])

    def test_reader_uses_committed_state_without_a_file_projection(self):
        with database_context(self.url):
            review(self.root, "session-a", "open", stage="S1", key="plan", artifact="plan.json")
            review(self.root, "session-a", "pause")
            self.assertEqual(read_current(self.root, "session-a")["state"], "paused")
            self.assertFalse((self.root / ".benchforge/projections").exists())


if __name__ == "__main__":
    unittest.main()
