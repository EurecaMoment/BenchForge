"""Keep user-installed sandbox discovery separate from the task environment."""
import os
from pathlib import Path
import json
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from server.task_workspace import run_task_code
from service_access.access_scope import TrustedAccess, access_context
from database.access import AccessScope


class TaskWorkspaceTests(unittest.TestCase):
    @unittest.skipUnless(os.name == 'posix', 'Task-code execution requires Linux descriptors')
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
            access = TrustedAccess(AccessScope('host', 'session', 'workspace', 'owner', 1, 1), str(root))
            with (
                patch.dict(os.environ, {"PATH": str(bindir), "SERVICE_SECRET": "fixture"}),
                patch("server.task_workspace.subprocess.Popen", return_value=process) as popen,
                access_context(access),
            ):
                result = run_task_code(artifacts, "sandbox-check", "print('ok')")
            args, kwargs = popen.call_args
            self.assertEqual(Path(args[0][0]), executable)
            self.assertIn("--unshare-all", args[0])
            self.assertEqual(kwargs["env"], {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"})
            self.assertEqual(result["returncode"], 0)
            self.assertNotIn('/harness', args[0])
            self.assertNotIn('/assets', args[0])
            self.assertNotIn('--ro-bind / /', ' '.join(args[0]))
            self.assertEqual(len(kwargs['pass_fds']), 2)
            self.assertEqual(args[0][args[0].index('--bind-fd') + 1], str(kwargs['pass_fds'][0]))
            self.assertEqual(args[0][args[0].index('--ro-bind-data') + 1], str(kwargs['pass_fds'][1]))
            self.assertIn('/run/benchforge-task.py', args[0])

    @unittest.skipUnless(os.name == 'posix', 'Task-code execution requires Linux descriptors')
    def test_output_and_receipt_symlinks_never_touch_external_targets(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / 'workspace'
            workspace.mkdir()
            victim = root / 'private.txt'
            victim.write_text('service-secret')
            access = TrustedAccess(AccessScope('host', 'session', 'workspace', 'owner', 1, 1), str(workspace))

            def execute(command, **kwargs):
                task = workspace / 'tasks/sample/work'
                script = next(task.glob('code_*.py'))
                for suffix in ('.stdout', '.stderr', '.receipt.json'):
                    script.with_suffix(suffix).symlink_to(victim)
                kwargs['stdout'].write(b'expected output')
                return Mock(returncode=0)

            with (access_context(access), patch('shutil.which', return_value='/usr/bin/bwrap'),
                  patch('server.task_workspace.subprocess.Popen', side_effect=execute)):
                result = run_task_code(root, 'sample', 'print(1)')
            self.assertEqual(victim.read_text(), 'service-secret')
            self.assertEqual(result['stdout'], 'expected output')
            self.assertNotIn('service-secret', json.dumps(result))
            task = Path(result['workspace_path'])
            self.assertFalse((task / (result['call_id'] + '.receipt.json')).is_symlink())

    @unittest.skipUnless(os.name == 'posix', 'Task-code execution requires Linux descriptors')
    def test_directory_replacement_does_not_redirect_output_or_mount(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / 'workspace'
            workspace.mkdir()
            outside = root / 'outside'
            outside.mkdir()
            access = TrustedAccess(AccessScope('host', 'session', 'workspace', 'owner', 1, 1), str(workspace))

            def execute(command, **kwargs):
                task = workspace / 'tasks/sample/work'
                original = task.with_name('original')
                task.rename(original)
                task.symlink_to(outside, target_is_directory=True)
                pinned = kwargs['pass_fds'][0]
                self.assertEqual(os.fstat(pinned).st_ino, original.stat().st_ino)
                self.assertEqual(command[command.index('--bind-fd') + 1], str(pinned))
                kwargs['stdout'].write(b'pinned output')
                return Mock(returncode=0)

            with (access_context(access), patch('shutil.which', return_value='/usr/bin/bwrap'),
                  patch('server.task_workspace.subprocess.Popen', side_effect=execute)):
                result = run_task_code(root, 'sample', 'print(1)')
            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual(result['stdout'], 'pinned output')
            self.assertTrue((workspace / 'tasks/sample/original' / (result['call_id'] + '.receipt.json')).is_file())

    @unittest.skipUnless(os.name == 'posix' and shutil.which('bwrap'), 'Live bubblewrap requires Linux runner')
    def test_real_runtime_keeps_numpy_and_denies_service_files(self):
        try:
            import numpy  # noqa: F401
        except ImportError:
            self.skipTest('NumPy is absent from the test interpreter')
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / 'workspace'
            workspace.mkdir()
            secret = root / 'service-secret.txt'
            secret.write_text('private')
            access = TrustedAccess(AccessScope('host', 'session', 'workspace', 'owner', 1, 1), str(workspace))
            code = ("import numpy as np\nfrom pathlib import Path\n"
                    f"assert not Path({str(secret)!r}).exists()\n"
                    "np.save('array.npy', np.arange(4))\nprint(int(np.arange(4).sum()))\n")
            with access_context(access):
                result = run_task_code(root, 'numpy-check', code)
            self.assertEqual(result['returncode'], 0, result['stderr'])
            self.assertEqual(result['stdout'].strip(), '6')
            self.assertTrue(any(row['file'] == 'array.npy' for row in result['artifacts']))

    def test_task_code_requires_host_identity(self):
        with patch('server.task_workspace.shutil.which', return_value='/usr/bin/bwrap'):
            with self.assertRaises(PermissionError):
                run_task_code('/tmp', 'sample', 'print(1)')

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
