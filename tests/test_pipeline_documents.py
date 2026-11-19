"""Edited workspace projections cannot approve or skip production steps."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest import skipUnless
import os
from unittest.mock import patch
from database.access import AccessScope
from service_access.access_scope import TrustedAccess, access_context
from pipelines.benchmark.execution.documents import RunDocuments


class PipelineDocumentsTests(TestCase):
    # 验证受管理运行不把可变文件状态副本导入为数据库权威状态。
    def test_managed_run_never_imports_mutable_state_projection(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'state.json').write_text('{"state":"completed"}')
            access = TrustedAccess(AccessScope('h', 's', 'w', 'o', 1, 1), tmp)
            with access_context(access), patch('pipelines.benchmark.execution.documents.engine_for'), \
                    patch('pipelines.benchmark.execution.documents.read_document', return_value=None):
                self.assertIsNone(RunDocuments(root).load('state.json'))

    # 验证数据库文档先提交，再更新文件副本，且后续写入使用正确版本。
    @skipUnless(os.name == 'posix', 'Scoped projection writes require Linux descriptors')
    def test_database_commit_precedes_projection_and_tracks_revision(self):
        with TemporaryDirectory() as tmp:
            access = TrustedAccess(AccessScope('h', 's', 'w', 'o', 1, 1), tmp)
            with access_context(access), patch('pipelines.benchmark.execution.documents.engine_for'), \
                    patch('pipelines.benchmark.execution.documents.read_document', return_value=None), \
                    patch('pipelines.benchmark.execution.documents.write_document', return_value={'revision': 1}) as save:
                docs = RunDocuments(tmp)
                docs.save('state.json', {'state': 'running'})
                self.assertEqual(save.call_args.kwargs['expected_revision'], 0)
                self.assertEqual(docs.revisions['state.json'], 1)
                self.assertIn('running', (Path(tmp) / 'state.json').read_text())
