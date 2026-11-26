"""Operation writers see private directories; users receive stable projections."""

from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, Mock, patch

from database.access import AccessScope
from service_access.access_scope import TrustedAccess, access_context
from storage.artifacts import write, read, jsonl
from storage.operation_outputs import operation_outputs, operation_workspace, logical_path, logical_value, persistent_outputs


class OperationOutputPathsTests(unittest.TestCase):
    # 验证无会话作用域的可信命令行操作仍直接使用原产物目录。
    def test_unscoped_cli_keeps_original_directory(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            with operation_outputs(root, 'plain', scoped=False) as directory:
                self.assertEqual(directory, root / 'operations/plain')
                self.assertEqual(logical_path(directory), directory)
                write(directory / 'result.json', {'complete': True})
            self.assertTrue((root / 'operations/plain/result.json').is_file())


@unittest.skipUnless(os.name == 'posix', 'Scoped publication requires Linux descriptors')
class ScopedOperationOutputTests(unittest.TestCase):
    # 建立会话工作区、外部目录及可信身份，供私有暂存与发布测试。
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.outside = self.root / 'outside'
        self.outside.mkdir()
        self.access = TrustedAccess(AccessScope('host', 'session', 'workspace', 'owner', 1, 1),
                                    str(self.workspace))

    # 验证评测与采集先写私有目录，发布回执返回稳定逻辑路径。
    def test_evaluation_and_capture_outputs_publish_stable_paths(self):
        with access_context(self.access), operation_outputs(self.workspace, 'capture', scoped=True) as directory:
            self.assertFalse(directory.is_relative_to(self.workspace))
            self.assertEqual(operation_workspace(directory), self.workspace)
            (directory / 'views').mkdir()
            (directory / 'views/rgb.png').write_bytes(b'captured-image')
            write(directory / 'metrics.json', {'score': 1})
            write(directory / 'result.json', {'image': str(directory / 'views/rgb.png'),
                                              'metrics': str(directory / 'metrics.json')})
            expected = self.workspace / 'operations/capture'
            self.assertEqual(logical_path(directory / 'metrics.json'), expected / 'metrics.json')
            self.assertEqual(logical_value({'output': str(directory)}), {'output': str(expected)})
        self.assertEqual((expected / 'views/rgb.png').read_bytes(), b'captured-image')
        self.assertEqual(read(expected / 'result.json')['metrics'], str(expected / 'metrics.json'))
        self.assertFalse(directory.exists())

    # 验证执行中替换公开目录不能重定向私有写入或最终发布。
    def test_changed_public_directory_cannot_redirect_private_writes(self):
        with access_context(self.access), operation_outputs(self.workspace, 'scene', scoped=True) as directory:
            public = self.workspace / 'operations/scene'
            original = public.with_name('original')
            public.rename(original)
            public.symlink_to(self.outside, target_is_directory=True)
            write(directory / 'result.json', {'path': str(directory / 'preview.png')})
            (directory / 'preview.png').write_bytes(b'image')
        self.assertEqual(list(self.outside.iterdir()), [])
        self.assertEqual((original / 'preview.png').read_bytes(), b'image')

    # 验证操作父目录已是链接时，在能力开始执行前就拒绝。
    def test_existing_symlink_is_rejected_before_capability_execution(self):
        (self.workspace / 'operations').symlink_to(self.outside, target_is_directory=True)
        with access_context(self.access), self.assertRaises(OSError), \
                operation_outputs(self.workspace, 'scene', scoped=True):
            self.fail('Unsafe directory was admitted')
        self.assertEqual(list(self.outside.iterdir()), [])

    # 验证能力执行失败时仍安全发布诊断产物，便于定位错误。
    def test_failed_capability_publishes_diagnostics(self):
        with access_context(self.access), self.assertRaisesRegex(ValueError, 'invalid scene'):
            with operation_outputs(self.workspace, 'failure', scoped=True) as directory:
                write(directory / 'diagnostic.json', {'problem': 'missing mesh'})
                raise ValueError('invalid scene')
        self.assertEqual(read(self.workspace / 'operations/failure/diagnostic.json'), {'problem': 'missing mesh'})

    # 验证能力在暂存树中创建的外部链接不会被复制发布。
    def test_link_created_by_capability_is_not_copied(self):
        secret = self.outside / 'private'
        secret.write_bytes(b'private')
        with access_context(self.access), self.assertRaises(PermissionError):
            with operation_outputs(self.workspace, 'link', scoped=True) as directory:
                (directory / 'leak').symlink_to(secret)
        self.assertFalse((self.workspace / 'operations/link/leak').exists())

    # 验证工作区操作在发布完成后保存稳定的产物位置。
    def test_workspace_jobs_commits_stable_artifact_path_after_publish(self):
        from storage.workspace_jobs import WorkspaceJobs

        class Runtime(WorkspaceJobs):
            # 在私有执行目录写入相互引用的采集与图像夹具，供逻辑路径转换验证。
            def execute(self, tool, args, directory):
                self.output_directory = directory
                write(directory / 'capture.json', {'image': str(directory / 'rgb.png')})
                (directory / 'rgb.png').write_bytes(b'image')
                return {'receipt': str(directory / 'capture.json')}

        runtime = Runtime.__new__(Runtime)
        runtime.root, runtime.workspace_id = self.workspace, 'workspace'
        runtime.dsh_context, runtime.engine = {}, MagicMock()
        with access_context(self.access):
            result = runtime._call('capture-fixture', {})
        public = Path(result['artifact_dir'])
        self.assertTrue(public.is_relative_to(self.workspace / 'operations'))
        self.assertTrue(Path(result['receipt']).is_file())
        self.assertFalse(runtime.output_directory.exists())
        statements = runtime.engine.begin.return_value.__enter__.return_value.execute.call_args_list
        inserted = statements[0].args[0].compile().params
        self.assertEqual(inserted['artifact'], str(public))

    # 验证流水线数据库键由逻辑运行路径生成，不随临时目录改变。
    def test_pipeline_database_identity_never_uses_temporary_directory(self):
        from pipelines.benchmark.execution.documents import RunDocuments
        import hashlib

        with (access_context(self.access), operation_outputs(self.workspace, 'pipeline', scoped=True) as directory,
              patch('pipelines.benchmark.execution.documents.engine_for'),
              patch('pipelines.benchmark.execution.documents.read_document', return_value=None),
              patch('pipelines.benchmark.execution.documents.write_document', return_value={'revision': 1}) as save):
            run = directory / 'pipeline_run'
            documents = RunDocuments(run)
            documents.save('state.json', {'output': str(run / 'steps/one/result.json')})
            public = self.workspace / 'operations/pipeline/pipeline_run'
            self.assertEqual(documents.key, hashlib.sha256(str(public).encode()).hexdigest())
            self.assertEqual(save.call_args.args[4], {'output': str(public / 'steps/one/result.json')})

    # 验证 JSON 原子发布不能经过父目录链接写到工作区外。
    def test_atomic_json_publication_does_not_write_through_parent_link(self):
        (self.workspace / 'tasks').symlink_to(self.outside, target_is_directory=True)
        with access_context(self.access), self.assertRaises(OSError):
            write(self.workspace / 'tasks/result.json', {'data': 'value'})
        self.assertEqual(list(self.outside.iterdir()), [])

    # 验证真实评分入口在私有执行后正常发布指标文件。
    def test_real_evaluation_score_publishes_its_metrics(self):
        from capabilities.evaluation.benchmark.reports import evaluate
        items, predictions = self.workspace / 'items.jsonl', self.workspace / 'predictions.jsonl'
        jsonl(items, [{'id': 'a', 'answer': 'A'}])
        jsonl(predictions, [{'id': 'a', 'answer': 'A'}])
        with access_context(self.access), operation_outputs(self.workspace, 'score', scoped=True) as directory:
            result = evaluate({'items': str(items), 'predictions': str(predictions)}, directory, {})
            result = logical_value(result)
        self.assertEqual(read(result['metrics'])['overall']['score'], 1)
        self.assertTrue(Path(result['scores']).is_file())

    # 验证真实 Isaac 查询工具经私有输出上下文发布回执。
    def test_real_isaac_query_publishes_its_receipt(self):
        from capabilities.isaac_sim.tool_api import execute
        from capabilities.isaac_sim.grpc import simulation_pb2 as pb
        with (access_context(self.access), operation_outputs(self.workspace, 'isaac', scoped=True) as directory,
              patch('capabilities.isaac_sim.tool_api.SimulationClient') as factory):
            factory.return_value.health.return_value = pb.HealthResponse(
                status='ready', api_version='isaacsim_rpc.v1')
            result = logical_value(execute({'action': 'health'}, directory, {}))
        self.assertEqual(read(result['receipt_path'])['status'], 'ready')
        self.assertTrue(Path(result['receipt_path']).is_relative_to(self.workspace / 'operations/isaac'))

    # 验证采集器分派把输出位置指向私有暂存目录。
    def test_real_collector_dispatch_writes_into_private_stage(self):
        from capabilities.data_acquisition.run_collector import collect

        # 检查采集命令的输出目录属于私有区域，并模拟写入图像产物。
        def launch(command, **_kwargs):
            output = Path(command[command.index('--output-dir') + 1])
            self.assertFalse(output.is_relative_to(self.workspace))
            (output / 'rgb.png').write_bytes(b'image')
            return Mock(returncode=0, pid=12345)

        with (access_context(self.access), operation_outputs(self.workspace, 'capture', scoped=True) as directory,
              patch('capabilities.data_acquisition.run_collector.subprocess.Popen', side_effect=launch),
              patch('capabilities.data_acquisition.run_collector.os.killpg')):
            result = logical_value(collect('habitat', {}, directory,
                {'collectors': {'habitat': {'command': ['fixture-python']}}}, self.workspace))
        self.assertEqual((Path(result['output_dir']) / 'rgb.png').read_bytes(), b'image')

    # 验证显式指定的运行在多次调用间恢复同一私有文件状态。
    def test_explicit_run_restores_private_outputs_across_calls(self):
        destination = self.workspace / 'operations/old/pipeline_run'
        config = {'artifacts': str(self.outside / 'service-artifacts')}
        with access_context(self.access), persistent_outputs(destination, config) as private:
            (private / 'rgb.png').write_bytes(b'original')
            write(private / 'state.json', {'image': str(private / 'rgb.png')})
            first_private = private
        self.assertEqual((destination / 'rgb.png').read_bytes(), b'original')
        (destination / 'rgb.png').write_bytes(b'user-edit')
        with access_context(self.access), persistent_outputs(destination, config) as private:
            self.assertEqual(private, first_private)
            self.assertEqual((private / 'rgb.png').read_bytes(), b'original')
            self.assertEqual(logical_path(private), destination)
            self.assertFalse(private.is_relative_to(self.workspace))
        self.assertEqual((destination / 'rgb.png').read_bytes(), b'original')

    # 验证嵌套流水线使用父运行私有树，保持父子产物引用可恢复。
    def test_nested_run_stays_inside_parent_private_tree(self):
        destination = self.workspace / 'operations/existing/pipeline_run'
        config = {'artifacts': str(self.outside / 'service-artifacts')}
        with access_context(self.access), persistent_outputs(destination, config) as parent:
            nested = parent / 'steps/nested/nested-run'
            with persistent_outputs(nested, config) as child:
                self.assertEqual(child, nested)
                write(child / 'result.json', {'output': str(child / 'rgb.png')})
                (child / 'rgb.png').write_bytes(b'nested')
            self.assertEqual((nested / 'rgb.png').read_bytes(), b'nested')
        public = destination / 'steps/nested/nested-run'
        self.assertEqual(read(public / 'result.json'), {'output': str(public / 'rgb.png')})

    # 验证恢复执行读取私有运行位置，对外仍返回原逻辑位置。
    def test_runner_uses_private_existing_run_but_returns_its_logical_path(self):
        from pipelines.benchmark.execution.runner import run
        destination = self.workspace / 'operations/existing/pipeline_run'
        config = {'artifacts': str(self.outside / 'service-artifacts')}

        # 断言执行器收到私有运行目录，并写入状态供返回路径检查。
        def execute(args, _directory, _config, _client):
            self.assertFalse(Path(args['run']).is_relative_to(self.workspace))
            write(Path(args['run']) / 'state.json', {'state': 'running'})
            return {'run': args['run'], 'state': 'running'}

        with access_context(self.access), patch('pipelines.benchmark.execution.runner._run_locked', side_effect=execute):
            result = run({'run': str(destination)}, self.workspace, config)
        self.assertEqual(result['run'], str(destination))
        self.assertEqual(read(destination / 'state.json'), {'state': 'running'})

    # 验证流水线恢复保留数据库权威状态及已完成步骤产物。
    def test_pipeline_resume_keeps_database_state_and_completed_output(self):
        from pipelines.benchmark.execution.runner import run
        from copy import deepcopy
        destination = self.workspace / 'operations/existing/pipeline_run'
        plan = self.workspace / 'plan.json'
        write(plan, {'inputs': {}, 'steps': [{'id': 'capture', 'uses': 'benchforge.observe'}]})
        database = {}

        # 从内存数据库夹具复制文档，避免测试通过共享对象绕过真实保存语义。
        def load(_engine, _scope, key, name):
            return deepcopy(database.get((key, name)))

        # 按预期修订递增版本并复制文档，模拟数据库的版本保存回执。
        def save(_engine, _scope, key, name, document, *, expected_revision):
            row = {'revision': expected_revision + 1, 'document': deepcopy(document)}
            database[key, name] = row
            return row

        # 向本次步骤目录写入采集文件并返回成功回执，供恢复跳过已完成步骤验证。
        def capture(_step, _arguments, directory, _environment, _client):
            (directory / 'rgb.png').write_bytes(b'captured')
            return {'image': str(directory / 'rgb.png'), 'state': 'SUCCEEDED'}

        config = {'artifacts': str(self.outside / 'service-artifacts')}
        args = {'run': str(destination), 'plan': str(plan), '_workspace': str(self.workspace)}
        with (access_context(self.access), patch('pipelines.benchmark.execution.documents.engine_for'),
              patch('pipelines.benchmark.execution.documents.read_document', side_effect=load),
              patch('pipelines.benchmark.execution.documents.write_document', side_effect=save),
              patch('pipelines.benchmark.execution.runner.load_policy', return_value={'execution_mode': 'exploration'}),
              patch('pipelines.benchmark.execution.runner.execute_step', side_effect=capture) as execute):
            first = run(args, self.workspace, config)
            (destination / 'state.json').write_text('{"state":"forged"}')
            (destination / 'steps/capture/rgb.png').write_bytes(b'edited')
            second = run(args, self.workspace, config)
        self.assertEqual(first['state'], 'completed')
        self.assertEqual(second['state'], 'completed')
        self.assertEqual(execute.call_count, 1)
        self.assertEqual((destination / 'steps/capture/rgb.png').read_bytes(), b'captured')
        self.assertTrue(second['outputs']['capture']['image'].startswith(str(destination)))


if __name__ == '__main__':
    unittest.main()
