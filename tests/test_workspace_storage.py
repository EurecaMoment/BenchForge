from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

from storage.dataset_partitions import claim_partition
from storage.operation_index import query_operations
from storage.native_jobs import NativeJobs


class WorkspaceStorageTests(unittest.TestCase):
    def setUp(self):
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

    def test_legacy_index_filters_session_before_limit(self):
        with closing(sqlite3.connect(self.root / "runs.sqlite")) as db, db:
            db.execute(
                "CREATE TABLE operations(id TEXT,tool TEXT,state TEXT,artifact TEXT,error TEXT)"
            )
            for i in range(10):
                folder = self.root / "operations" / str(i)
                folder.mkdir(parents=True)
                (folder / "request.json").write_text(
                    json.dumps({"context": {"session_id": "a" if i == 0 else "b"}})
                )
                db.execute(
                    "INSERT INTO operations VALUES (?,?,?,?,?)",
                    (str(i), "plan", "completed", str(folder), None),
                )
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
        with closing(sqlite3.connect(self.root / "runs.sqlite")) as db, db:
            db.execute(
                "CREATE TABLE operations(id TEXT,tool TEXT,state TEXT,artifact TEXT,error TEXT)"
            )
            db.execute(
                "INSERT INTO operations VALUES (?,?,?,?,?)",
                ("old", "isaac_sim", "completed", str(operation), None),
            )
        owners = NativeJobs(self.root)
        with self.assertRaises(PermissionError):
            owners.require("sim:50051", "job-old", "session-other")
        with self.assertRaises(PermissionError):
            owners.require("different:50051", "job-old", "session-owner")
        self.assertEqual(owners.require("sim:50051", "job-old", "session-owner"), "request-old")
