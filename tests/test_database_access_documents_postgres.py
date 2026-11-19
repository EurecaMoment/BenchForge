"""The pipeline authority survives edits to filesystem projections."""

from pathlib import Path
import tempfile
import unittest

from database.engine import engine_for
from storage.access_identity import register_session_workspace
from storage.access_documents import read_document, write_document, DocumentConflict
from tests.database_fixtures import use_postgres


class PipelineDocumentsTests(unittest.TestCase):
    # 创建独立数据库与会话作用域，用于验证数据库文档的权威和并发版本。
    def setUp(self):
        self.engine = engine_for(use_postgres(self))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.scope = register_session_workspace(self.engine, host_id="host", session_id="session",
            owner_id="owner", root_path=self.root)

    # 验证修改文件副本不能改变权威文档，写入必须匹配已读版本。
    def test_filesystem_projection_cannot_change_authority_and_updates_use_cas(self):
        saved = write_document(self.engine, self.scope, "pipeline-1", "state.json", {"status": "waiting_review"}, expected_revision=0)
        (self.root / "state.json").write_text('{"status":"passed"}')
        self.assertEqual(read_document(self.engine, self.scope, "pipeline-1", "state.json"), saved)
        with self.assertRaises(DocumentConflict):
            write_document(self.engine, self.scope, "pipeline-1", "state.json", {"status": "passed"}, expected_revision=0)
        next_value = write_document(self.engine, self.scope, "pipeline-1", "state.json", {"status": "running"}, expected_revision=1)
        self.assertEqual(next_value["revision"], 2)
        other = self.root / "other"
        other.mkdir()
        scope = register_session_workspace(self.engine, host_id="host", session_id="another", owner_id="owner", root_path=other)
        self.assertIsNone(read_document(self.engine, scope, "pipeline-1", "state.json"))
