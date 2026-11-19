"""Dataset imports cannot promote foreign references into conversation artifacts."""

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from capabilities.data_acquisition.evidence import import_evidence
from capabilities.data_acquisition.preparation import acquire, normalize
from capabilities.data_acquisition.source_files import source_rows
from database.access import AccessScope
from service_access.access_scope import TrustedAccess, access_context


class DataSourceAccessTests(unittest.TestCase):
    # 建立会话工作区、外部资源与私有输出目录，隔离数据来源权限测试。
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.output = self.workspace / 'output'
        self.output.mkdir()
        self.access = TrustedAccess(
            AccessScope('host', 'session-a', 'workspace-a', 'owner', 1, 1), str(self.workspace))

    # 在当前工作区写入单条 JSONL 输入，返回其文件路径。
    def records(self, row):
        path = self.workspace / 'input.jsonl'
        path.write_text(json.dumps(row) + '\n', encoding='utf-8')
        return path

    # 验证顶层导入来源受会话根约束，不能读取外部 JSONL。
    def test_top_level_source_cannot_leave_workspace(self):
        foreign = self.root / 'other.jsonl'
        foreign.write_text('{}\n', encoding='utf-8')
        with access_context(self.access), self.assertRaises(PermissionError):
            source_rows(foreign)

    # 验证下载文件名不能通过绝对路径或父目录分量选择输出位置。
    def test_download_filename_cannot_select_a_destination(self):
        with patch('urllib.request.urlopen') as request:
            for filename in ('../escape.json', '/tmp/escape.json', '..\\escape.json', 'C:escape.json'):
                with self.subTest(filename=filename), self.assertRaises(ValueError):
                    acquire({'url': 'https://example.com/data', 'filename': filename}, self.output, {})
            request.assert_not_called()

    # 验证数据下载拒绝本地 file URL，且不会发起请求。
    def test_download_does_not_accept_local_file_urls(self):
        with patch('urllib.request.urlopen') as request, self.assertRaises(ValueError):
            acquire({'url': (self.root / 'private.json').as_uri()}, self.output, {})
        request.assert_not_called()

    # 验证记录中的嵌套媒体不能复制其他工作区的字节。
    @unittest.skipUnless(os.name == 'posix', 'Descriptor-relative file access requires Linux')
    def test_nested_foreign_media_is_never_copied(self):
        foreign = self.root / 'private.png'
        foreign.write_bytes(b'FOREIGN_AUDIT_SENTINEL')
        source = self.records({'id': 'one', 'media': [str(foreign)]})
        with access_context(self.access), self.assertRaises(ValueError):
            normalize({'input': str(source)}, self.output, {})
        self.assertEqual(list((self.output / 'media').iterdir()), [])

    # 验证当前工作区内的相对媒体引用仍可正常导入。
    @unittest.skipUnless(os.name == 'posix', 'Descriptor-relative file access requires Linux')
    def test_authorized_relative_media_still_imports(self):
        media = self.workspace / 'source.png'
        media.write_bytes(b'OWN_AUDIT_SENTINEL')
        source = self.records({'id': 'one', 'media': ['source.png'], 'answer': 'yes'})
        with access_context(self.access):
            result = normalize({'input': str(source)}, self.output, {})
            records = source_rows(result['input'])
        self.assertEqual(Path(records[0]['media'][0]).read_bytes(), b'OWN_AUDIT_SENTINEL')
        self.assertEqual(records[0]['answer'], 'yes')

    # 验证证据导入同时校验来源文件、媒体和资源引用的所有权。
    @unittest.skipUnless(os.name == 'posix', 'Descriptor-relative file access requires Linux')
    def test_evidence_checks_provenance_media_and_assets(self):
        own = self.workspace / 'facts.json'
        own.write_text('{"answer": 42}', encoding='utf-8')
        foreign = self.root / 'private.json'
        foreign.write_text('{"answer": "FOREIGN_AUDIT_SENTINEL"}', encoding='utf-8')
        for field in ('provenance', 'media', 'assets'):
            row = {'id': 'one', 'provenance': {'kind': 'program', 'path': str(own)},
                   'selectors': {'answer': '/answer'}, 'media': [], 'assets': []}
            if field == 'provenance':
                row[field]['path'] = str(foreign)
            else:
                row[field] = [str(foreign)]
            with self.subTest(field=field), access_context(self.access), self.assertRaises(PermissionError):
                import_evidence({'input': str(self.records(row))}, self.output)

    # 验证指向外部文件的媒体链接不会绕过来源约束。
    @unittest.skipUnless(os.name == 'posix', 'Descriptor-relative file access requires Linux')
    def test_symlink_media_does_not_import_foreign_bytes(self):
        foreign = self.root / 'private.png'
        foreign.write_bytes(b'FOREIGN_AUDIT_SENTINEL')
        (self.workspace / 'link.png').symlink_to(foreign)
        source = self.records({'id': 'one', 'media': ['link.png']})
        with access_context(self.access), self.assertRaises(ValueError):
            normalize({'input': str(source)}, self.output, {})
        self.assertEqual(list((self.output / 'media').iterdir()), [])
