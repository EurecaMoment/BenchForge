from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest


class PublicMeasurementBundleTests(unittest.TestCase):
    # 创建独立临时目录，供公开测量代码便携运行验证。
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    # 验证公开测量及两个缓存模块组成的小包无需完整服务仓库即可执行。
    def test_public_measurements_run_as_a_three_file_bundle(self):
        source = (
            Path(__file__).resolve().parents[1] / "src/capabilities/data_acquisition/measurements"
        )
        for name in (
            "public_measurements.py",
            "public_geometry_cache.py",
            "public_sensor_cache.py",
        ):
            shutil.copy2(source / name, self.root / name)
        command = f"import sys; sys.path.insert(0, {str(self.root)!r}); from public_measurements import PublicReconstruction"
        subprocess.run([sys.executable, "-I", "-B", "-c", command], check=True, capture_output=True)
