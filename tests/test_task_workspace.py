"""Keep user-installed sandbox discovery separate from the task environment."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from server.task_workspace import run_task_code


class TaskWorkspaceTests(unittest.TestCase):
    def test_user_installed_runner_does_not_leak_service_environment(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            bindir = root / "bin"
            bindir.mkdir()
            executable = bindir / ("bwrap.exe" if os.name == "nt" else "bwrap")
            executable.touch()
            executable.chmod(0o755)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            process = Mock(returncode=0)
            with (
                patch.dict(os.environ, {"PATH": str(bindir), "SERVICE_SECRET": "fixture"}),
                patch("server.task_workspace.subprocess.Popen", return_value=process) as popen,
            ):
                result = run_task_code(artifacts, "sandbox-check", "print('ok')")
            args, kwargs = popen.call_args
            self.assertEqual(Path(args[0][0]), executable)
            self.assertIn("--unshare-all", args[0])
            self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
            self.assertEqual(result["returncode"], 0)

    def test_missing_runner_fails_before_writing_or_starting_task(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with (
                patch.dict(os.environ, {"PATH": str(root)}),
                patch("server.task_workspace.subprocess.Popen") as popen,
            ):
                with self.assertRaisesRegex(RuntimeError, "requires bubblewrap"):
                    run_task_code(root, "sandbox-check", "print('ok')")
            popen.assert_not_called()
            self.assertEqual(list(root.iterdir()), [])
