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

    def test_existing_symlink_is_rejected_before_capability_execution(self):
        (self.workspace / 'operations').symlink_to(self.outside, target_is_directory=True)
        with access_context(self.access), self.assertRaises(OSError), \
                operation_outputs(self.workspace, 'scene', scoped=True):
            self.fail('Unsafe directory was admitted')
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_failed_capability_publishes_diagnostics(self):
        with access_context(self.access), self.assertRaisesRegex(ValueError, 'invalid scene'):
            with operation_outputs(self.workspace, 'failure', scoped=True) as directory:
                write(directory / 'diagnostic.json', {'problem': 'missing mesh'})
                raise ValueError('invalid scene')
        self.assertEqual(read(self.workspace / 'operations/failure/diagnostic.json'), {'problem': 'missing mesh'})

    def test_link_created_by_capability_is_not_copied(self):
        secret = self.outside / 'private'
        secret.write_bytes(b'private')
        with access_context(self.access), self.assertRaises(PermissionError):
            with operation_outputs(self.workspace, 'link', scoped=True) as directory:
                (directory / 'leak').symlink_to(secret)
        self.assertFalse((self.workspace / 'operations/link/leak').exists())

    def test_workspace_jobs_commits_stable_artifact_path_after_publish(self):
        from storage.workspace_jobs import WorkspaceJobs

        class Runtime(WorkspaceJobs):
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

    def test_atomic_json_publication_does_not_write_through_parent_link(self):
        (self.workspace / 'tasks').symlink_to(self.outside, target_is_directory=True)
        with access_context(self.access), self.assertRaises(OSError):
            write(self.workspace / 'tasks/result.json', {'data': 'value'})
        self.assertEqual(list(self.outside.iterdir()), [])

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

    def test_real_isaac_query_publishes_its_receipt(self):
        from capabilities.isaac_sim.tool_api import execute
        from capabilities.isaac_sim.grpc import simulator_pb2 as pb
        with (access_context(self.access), operation_outputs(self.workspace, 'isaac', scoped=True) as directory,
              patch('capabilities.isaac_sim.tool_api.A100Client') as factory,
              patch('capabilities.isaac_sim.tool_api.NativeResources')):
            factory.return_value.call.return_value = pb.Capabilities(api_version='fixture')
            result = logical_value(execute({'action': 'capabilities'}, directory, {}))
        self.assertEqual(read(result['receipt_path'])['api_version'], 'fixture')
        self.assertTrue(Path(result['receipt_path']).is_relative_to(self.workspace / 'operations/isaac'))

    def test_real_collector_dispatch_writes_into_private_stage(self):
        from capabilities.data_acquisition.run_collector import collect

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

    def test_runner_uses_private_existing_run_but_returns_its_logical_path(self):
        from pipelines.benchmark.execution.runner import run
        destination = self.workspace / 'operations/existing/pipeline_run'
        config = {'artifacts': str(self.outside / 'service-artifacts')}

        def execute(args, _directory, _config, _client):
            self.assertFalse(Path(args['run']).is_relative_to(self.workspace))
            write(Path(args['run']) / 'state.json', {'state': 'running'})
            return {'run': args['run'], 'state': 'running'}

        with access_context(self.access), patch('pipelines.benchmark.execution.runner._run_locked', side_effect=execute):
            result = run({'run': str(destination)}, self.workspace, config)
        self.assertEqual(result['run'], str(destination))
        self.assertEqual(read(destination / 'state.json'), {'state': 'running'})

    def test_pipeline_resume_keeps_database_state_and_completed_output(self):
        from pipelines.benchmark.execution.runner import run
        from copy import deepcopy
        destination = self.workspace / 'operations/existing/pipeline_run'
        plan = self.workspace / 'plan.json'
        write(plan, {'inputs': {}, 'steps': [{'id': 'capture', 'uses': 'benchforge.observe'}]})
        database = {}

        def load(_engine, _scope, key, name):
            return deepcopy(database.get((key, name)))

        def save(_engine, _scope, key, name, document, *, expected_revision):
            row = {'revision': expected_revision + 1, 'document': deepcopy(document)}
            database[key, name] = row
            return row

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
