"""公共 JSON 写入在失败、并发读取及独立报告分发时保持一致。"""
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from storage import artifacts, files, json_io


class JsonPublicationTests(unittest.TestCase):
    def test_public_writers_publish_the_same_strict_document(self):
        """历史写入入口共用严格格式，拒绝非有限值且保留原文件。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for index, writer in enumerate((files.write_json, artifacts.write_json, json_io.write_json)):
                path = root / f'{index}.json'
                writer(path, {'title': '中文', 'value': 1.25})
                original = path.read_bytes()
                self.assertTrue(original.endswith(b'\n'))
                self.assertEqual(json.loads(original), {'title': '中文', 'value': 1.25})
                for invalid in (float('nan'), float('inf'), -float('inf')):
                    with self.assertRaises(ValueError):
                        writer(path, {'nested': [invalid]})
                    self.assertEqual(path.read_bytes(), original)
            self.assertEqual(len({file.read_bytes() for file in root.iterdir()}), 1)
            self.assertEqual(len(list(root.iterdir())), 3)

    def test_failed_jsonl_record_does_not_publish_partial_output(self):
        """批量第二行不合法时仍保留整份旧产物，而不是留下半份 JSONL。"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'records.jsonl'
            artifacts.write_jsonl(path, [{'saved': True}])
            before = path.read_bytes()
            with self.assertRaises(ValueError):
                artifacts.write_jsonl(path, [{'new': True}, {'number': float('nan')}])
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.iterdir()), [path])

    def test_replace_failure_keeps_old_file_and_cleans_temporary_file(self):
        """文件发布失败不破坏旧内容；成功覆盖保留原文件权限。"""
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'output.json'
            json_io.write_json(path, {'saved': True})
            before = path.read_bytes()
            mode = stat.S_IMODE(path.stat().st_mode)
            with patch('storage.json_io.os.replace', side_effect=OSError('publish failed')):
                with self.assertRaisesRegex(OSError, 'publish failed'):
                    json_io.write_json(path, {'new': True})
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(list(path.parent.iterdir()), [path])
            json_io.write_json(path, {'new': True})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)

    def test_exported_helpers_work_without_benchforge_or_database_dependencies(self):
        """报告副本仅用标准库即可写入评分文件，不依赖仓库或数据库包。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = Path(__file__).resolve().parents[1] / 'src'
            for relative in ('storage/artifacts.py', 'storage/json_io.py', 'service_access/processes.py'):
                shutil.copyfile(source / relative, root / Path(relative).name)
            result = subprocess.run([sys.executable, '-I', '-c',
                'import sys;sys.path.insert(0,sys.argv[1]);from artifacts import write;'
                'write(sys.argv[2],{"score":1})', str(root), str(root / 'metrics.json')],
                text=True, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((root / 'metrics.json').read_text()), {'score': 1})
