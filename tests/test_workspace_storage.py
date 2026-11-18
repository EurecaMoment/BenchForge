from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from tests.database_fixtures import use_postgres

from database.engine import engine_for, workspace_id
from database.schema import operations

from storage.dataset_partitions import claim_partition
from storage.operation_index import query_operations
from storage.native_jobs import NativeJobs


class WorkspaceStorageTests(unittest.TestCase):
    def setUp(self):
        self.database_url = use_postgres(self)
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_concurrent_partition_claims_publish_one_immutable_split(self):
        barrier = Barrier(8)

        def claim(index):
            barrier.wait()
            try:
                return claim_partition(self.root, "scene", "train" if index % 2 else "test")
            except ValueError:
                return None

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(claim, range(8)))
        self.assertEqual(len({x for x in results if x}), 1)
        self.assertEqual(results.count(None), 4)

    def test_index_filters_session_before_limit(self):
        with engine_for(self.database_url).begin() as db:
            for i in range(10):
                db.execute(operations.insert().values(workspace_id=workspace_id(self.root),
                    id=str(i), tool='plan', state='completed', artifact='operations/' + str(i),
                    session_id='a' if i == 0 else 'b'))
        self.assertEqual(
            [row["id"] for row in query_operations(self.root, sessions=["a"], limit=2)], ["0"]
        )
        self.assertEqual(len(query_operations(self.root, sessions=["b"], limit=2)), 2)
        self.assertEqual(query_operations(self.root), [])

    def test_old_native_job_recovery_requires_matching_session_and_endpoint(self):
        operation = self.root / "operations" / "old"
        operation.mkdir(parents=True)
        receipt = self.root / ".benchforge/isaac_sim/old/receipt.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text(json.dumps({"job_id": "job-old", "request_id": "request-old"}))
        receipt.with_name("request.json").write_text(
            json.dumps({"endpoint": "sim:50051", "request_id": "request-old"})
        )
        (operation / "request.json").write_text(
            json.dumps(
                {"context": {"session_id": "session-owner"}, "arguments": {"action": "submit"}}
            )
        )
        (operation / "result.json").write_text(
            json.dumps({"job": {"job_id": "job-old"}, "receipt_path": str(receipt)})
        )
        with engine_for(self.database_url).begin() as db:
            db.execute(operations.insert().values(workspace_id=workspace_id(self.root),
                id='old', tool='isaac_sim', state='completed', artifact=str(operation), session_id='session-owner'))
        owners = NativeJobs(self.root)
        with self.assertRaises(PermissionError):
            owners.require("sim:50051", "job-old", "session-other")
        with self.assertRaises(PermissionError):
            owners.require("different:50051", "job-old", "session-owner")
        self.assertEqual(owners.require("sim:50051", "job-old", "session-owner"), "request-old")
