"""Descriptor-based task input/output resists mutable paths and link targets."""

from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from storage.confined_files import copy_output, mkdir, open_directory, open_read, open_write


@unittest.skipUnless(os.name == 'posix', 'Confined files require Linux descriptors')
class ConfinedFileTests(unittest.TestCase):
    # 建立工作区、外部目标及可信身份，供文件描述符约束测试。
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.outside = self.root / 'outside'
        self.outside.mkdir()

    # 验证创建任务目录不能沿已有父目录链接写入工作区外。
    def test_mkdir_cannot_follow_an_existing_parent_link(self):
        (self.workspace / 'tasks').symlink_to(self.outside, target_is_directory=True)
        with self.assertRaises(OSError):
            mkdir(self.workspace, 'tasks/new/work')
        self.assertEqual(list(self.outside.iterdir()), [])

    # 验证输出替换符号或硬链接目录项时不会改写原目标字节。
    def test_output_replaces_link_entry_without_writing_its_target(self):
        victim = self.outside / 'secret'
        victim.write_bytes(b'private')
        for name, hard in (('symbolic', False), ('hard', True)):
            target = self.workspace / name
            target.hardlink_to(victim) if hard else target.symlink_to(victim)
            copy_output(self.workspace, BytesIO(b'public'), name)
            self.assertEqual(target.read_bytes(), b'public')
            self.assertEqual(victim.read_bytes(), b'private')

    # 验证父目录在写入到原子发布期间被固定，目录替换不改变目的地。
    def test_write_parent_is_pinned_until_atomic_publication(self):
        mkdir(self.workspace, 'tasks/work')
        task = self.workspace / 'tasks/work'
        with open_write(self.workspace, 'tasks/work/result') as stream:
            task.rename(task.with_name('original'))
            task.symlink_to(self.outside, target_is_directory=True)
            stream.write(b'result')
        self.assertEqual(list(self.outside.iterdir()), [])
        self.assertEqual((task.with_name('original') / 'result').read_bytes(), b'result')

    # 验证发布失败保留旧结果并移除未完成的临时文件。
    def test_failed_publication_keeps_old_result_and_removes_temporary_file(self):
        target = self.workspace / 'result'
        target.write_bytes(b'old')
        with self.assertRaises(ValueError):
            copy_output(self.workspace, BytesIO(b'too big'), 'result', limit=2)
        self.assertEqual(target.read_bytes(), b'old')
        self.assertEqual(list(self.workspace.iterdir()), [target])

    # 验证描述符根下的读写都拒绝绝对外部路径及父目录穿越。
    def test_descriptor_root_reads_and_writes_reject_parent_traversal(self):
        with open_directory(self.workspace) as descriptor:
            for path in ('../outside/secret', str(self.outside / 'secret')):
                with self.assertRaises(PermissionError), open_write(descriptor, path):
                    pass
                with self.assertRaises(PermissionError), open_read(descriptor, path):
                    pass
            mkdir(descriptor, 'tasks/work')
            copy_output(descriptor, BytesIO(b'ok'), 'tasks/work/result')
            with open_read(descriptor, 'tasks/work/result') as source:
                self.assertEqual(source.read(), b'ok')


if __name__ == '__main__':
    unittest.main()
