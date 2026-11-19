from concurrent.futures import ProcessPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from storage.execution_lock import exclusive_run, RunBusyError
from storage.file_lock import file_lock


# 在跨进程锁保护下反复更新计数文件，供丢失更新检测。
def increment(directory):
    root = Path(directory)
    for _ in range(10):
        with file_lock(root / ".lock"):
            file = root / "count"
            count = int(file.read_text()) if file.exists() else 0
            file.write_text(str(count + 1))


class FileLockTests(unittest.TestCase):
    # 验证多个进程竞争最初为空的锁文件时仍能串行更新计数。
    def test_competing_processes_lock_an_initially_empty_marker(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with ProcessPoolExecutor(max_workers=4, mp_context=get_context("spawn")) as pool:
                list(pool.map(increment, [temporary] * 4))
            self.assertEqual((root / "count").read_text(), "40")
            self.assertEqual((root / ".lock").stat().st_size, 0)
            with exclusive_run(root):
                with self.assertRaises(RunBusyError), exclusive_run(root):
                    pass
                with exclusive_run(root / "other"):
                    pass
