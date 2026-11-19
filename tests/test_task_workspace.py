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
    # 验证隔离命令分别挂载会话只读输入与任务可写输出命名空间。
    def test_command_preserves_conversation_input_and_task_output_namespaces(self):
        from server.task_runtime import python_command

        with TemporaryDirectory() as temporary, patch('shutil.which', return_value='/usr/bin/bwrap'):
            root = Path(temporary).resolve()
            task = root / 'tasks/sample/work'
            script = task / 'code.py'
            command = python_command(task, script, workspace_fd=10, script_fd=11,
                                     conversation_root=root, conversation_fd=12)
            self.assertEqual(command[command.index('--ro-bind-fd') + 1:command.index('--ro-bind-fd') + 3],
                             ['12', str(root)])
            self.assertEqual(command[command.index('--bind-fd') + 1:command.index('--bind-fd') + 3],
                             ['10', str(task)])
            self.assertEqual(command[command.index('--chdir') + 1], str(task))
            with self.assertRaises(PermissionError):
                python_command(root.parent / 'other', root.parent / 'other/code.py',
                               workspace_fd=10, script_fd=11, conversation_root=root, conversation_fd=12)

    # 验证从用户安装位置启动执行器时不泄漏服务环境变量。
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
            self.assertEqual(len(kwargs['pass_fds']), 3)
            self.assertEqual(args[0][args[0].index('--bind-fd') + 1], str(kwargs['pass_fds'][0]))
            self.assertEqual(args[0][args[0].index('--ro-bind-data') + 1], str(kwargs['pass_fds'][1]))
            self.assertEqual(args[0][args[0].index('--ro-bind-fd') + 1], str(kwargs['pass_fds'][2]))
            self.assertEqual(args[0][args[0].index('--ro-bind-fd') + 2], str(root))
            self.assertIn('/run/benchforge-task.py', args[0])

    # 验证标准输出、错误和回执的链接不能改写外部目标。
    @unittest.skipUnless(os.name == 'posix', 'Task-code execution requires Linux descriptors')
    def test_output_and_receipt_symlinks_never_touch_external_targets(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / 'workspace'
            workspace.mkdir()
            victim = root / 'private.txt'
            victim.write_text('service-secret')
            access = TrustedAccess(AccessScope('host', 'session', 'workspace', 'owner', 1, 1), str(workspace))

            # 在模拟执行时为各输出创建外部链接，复现任务产物替换竞争。
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

    # 验证运行中替换任务目录不会重定向挂载或产物发布。
    @unittest.skipUnless(os.name == 'posix', 'Task-code execution requires Linux descriptors')
    def test_directory_replacement_does_not_redirect_output_or_mount(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / 'workspace'
            workspace.mkdir()
            outside = root / 'outside'
            outside.mkdir()
            access = TrustedAccess(AccessScope('host', 'session', 'workspace', 'owner', 1, 1), str(workspace))

            # 将任务目录改名并用外部链接替代，模拟执行期间的目录替换。
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

    # 验证真实隔离运行仍能导入 NumPy，同时拒绝读取服务私有文件。
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

    # 验证真实运行可读取本会话输入，返回路径可供后续自有工具复用。
    @unittest.skipUnless(os.name == 'posix' and shutil.which('bwrap'), 'Live bubblewrap requires Linux runner')
    def test_real_runtime_reads_conversation_inputs_and_returns_reusable_paths(self):
        from capabilities.asset_generation.generation import GenerationTools
        from capabilities.scene_authoring.program_submission import read_program_document

        with TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            workspace = root / 'session-a'
            workspace.mkdir()
            sibling = workspace / 'tasks/prepare/work'
            sibling.mkdir(parents=True)
            source = sibling / 'mesh.obj'
            source.write_text('v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n')
            other = root / 'session-b'
            other.mkdir()
            (other / 'secret.json').write_text('{"private":true}')
            service = root / 'service.py'
            service.write_text('private_service_code = True')
            access = TrustedAccess(AccessScope('host', 'session-a', 'workspace-a', 'owner', 1, 1), str(workspace))
            code = ("from pathlib import Path\nimport json\n"
                    f"source = Path({str(source)!r})\n"
                    "assert source.read_text().startswith('v 0 0 0')\n"
                    "try:\n    source.write_text('changed')\n"
                    "except OSError:\n    pass\n"
                    "else:\n    raise AssertionError('conversation input was writable')\n"
                    f"assert not Path({str(other / 'secret.json')!r}).exists()\n"
                    f"assert not Path({str(service)!r}).exists()\n"
                    "Path('scene.json').write_text(json.dumps({'objects':[]}))\n"
                    "Path('mesh.obj').write_bytes(source.read_bytes())\n")
            with access_context(access):
                result = run_task_code(root / 'private', 'consume', code)
                self.assertEqual(result['returncode'], 0, result['stderr'])
                files = {row['file']: row['source_path'] for row in result['artifacts']}
                submitted = read_program_document(root / 'private', files['scene.json'])
                self.assertEqual(submitted['program'], {'objects': []})
                imported = GenerationTools(root / 'private').source_path(files['mesh.obj'])
                self.assertEqual(imported.read_bytes(), source.read_bytes())
                self.assertEqual(result['conversation_path'], str(workspace))
            for file in (root / 'private').rglob('*'):
                if file.is_file():
                    file.chmod(0o600)

    # 验证任务代码执行必须具备宿主已验证身份。
    def test_task_code_requires_host_identity(self):
        with patch('server.task_workspace.shutil.which', return_value='/usr/bin/bwrap'):
            with self.assertRaises(PermissionError):
                run_task_code('/tmp', 'sample', 'print(1)')

    # 验证缺少隔离执行器时，在写入脚本或启动任务前失败。
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
