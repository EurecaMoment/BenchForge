"""Workbench run snapshots share the runner's PostgreSQL authority and scope."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from database.engine import engine_for
from pipelines.benchmark.execution.documents import RunDocuments
from service_access.access_scope import TrustedAccess, access_context
from storage.access_identity import register_session_workspace, revoke_scope
from storage.pipeline_queries import query_pipeline_runs, latest_production_execution
from tests.database_fixtures import use_postgres


class WorkbenchRunQueryTests(unittest.TestCase):
    def test_latest_execution_uses_registered_database_state_and_retains_completion(self):
        """续接查询忽略嵌套运行及文件篡改，完整结束后不回捞较早失败记录。"""
        access = self.scopes['a']; root = Path(access.root_path)
        run = root / 'operations/production/pipeline_run'
        plan = {'production_contract': 'benchforge.production-stages/v1',
                'stage_review_key': str(run.parent / 'plan.json'),
                'steps': [{'id': 'scene', 'uses': 'scene.acceptance'}]}
        with access_context(access):
            docs = RunDocuments(run)
            docs.save('plan.json', plan)
            docs.save('state.json', {'state': 'needs_evidence', 'run_path': str(run),
                                    'steps': {'scene': {'state': 'needs_evidence'}}})
            nested = RunDocuments(run / 'nested')
            nested.save('plan.json', {'steps': []})
            nested.save('state.json', {'state': 'running'})
        result = latest_production_execution(root, 'session-a')
        self.assertEqual(result['run'], str(run))
        self.assertEqual(result['state']['state'], 'needs_evidence')
        (run / 'state.json').write_text('{"state":"completed"}', encoding='utf8')
        self.assertEqual(latest_production_execution(root, 'session-a')['state']['state'], 'needs_evidence')
        with access_context(access):
            docs.save('state.json', {'state': 'completed', 'run_path': str(run), 'steps': {}})
        self.assertEqual(latest_production_execution(root, 'session-a')['state']['state'], 'completed')
        self.assertIsNone(latest_production_execution(self.scopes['b'].root_path, 'session-b'))
        revoke_scope(self.engine, access.scope)
        self.assertIsNone(latest_production_execution(root, 'session-a'))

    def setUp(self):
        """为每个用例准备独立数据库及两个真实登记的会话工作区。"""
        self.database_url = use_postgres(self)
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.engine = engine_for(self.database_url)
        self.scopes = {}
        for name in ('a', 'b'):
            root = self.root / name
            root.mkdir()
            scope = register_session_workspace(self.engine, host_id='host-test', session_id='session-' + name,
                owner_id='owner-test', root_path=str(root))
            self.scopes[name] = TrustedAccess(scope, str(root))

    def save_run(self, name, *, state='running'):
        """通过真实执行器文档接口写入运行，让查询与执行测试同一份权威记录。"""
        access = self.scopes[name]
        directory = Path(access.root_path) / 'operations/run/pipeline_run'
        directory.mkdir(parents=True)
        with access_context(access):
            documents = RunDocuments(directory)
            documents.save('plan.json', {'steps': [{'id': 'collect', 'uses': 'collection.validate'}]})
            documents.save('state.json', {'state': state, 'steps': {'collect': {'state': 'running'}}})
        return directory

    def test_database_state_survives_modified_and_missing_file_projections(self):
        """篡改完成状态并删除计划副本后，右栏查询仍返回执行器保存的计划和进度。"""
        directory = self.save_run('a')
        (directory / 'state.json').write_text(json.dumps({'state': 'completed'}), encoding='utf-8')
        (directory / 'plan.json').unlink()
        rows = query_pipeline_runs(self.scopes['a'].root_path, [str(directory), str(directory)],
            sessions=['session-a'], database_url=self.database_url)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['state']['state'], 'running')
        self.assertEqual(rows[0]['plan']['steps'][0]['id'], 'collect')
        self.assertEqual(rows[0]['authority'], 'postgresql')
        self.assertEqual(rows[0]['revisions'], {'plan': 1, 'state': 1})
        self.assertTrue(rows[0]['updated_at'])

    def test_foreign_unregistered_and_revoked_runs_do_not_fall_back_to_files(self):
        """其他会话、撤销工作区和仅有文件的伪造运行都不能成为可信状态。"""
        directory = self.save_run('a')
        root = self.scopes['a'].root_path
        self.assertEqual(query_pipeline_runs(root, [str(directory)], sessions=['session-b'],
            database_url=self.database_url), [])
        fabricated = Path(root) / 'forged'
        fabricated.mkdir()
        (fabricated / 'state.json').write_text('{"state":"completed"}', encoding='utf-8')
        self.assertEqual(query_pipeline_runs(root, [str(fabricated)], sessions=['session-a'],
            database_url=self.database_url), [])
        revoke_scope(self.engine, self.scopes['a'].scope)
        self.assertEqual(query_pipeline_runs(root, [str(directory)], sessions=['session-a'],
            database_url=self.database_url), [])

    def test_query_bounds_workspace_and_requires_a_session_or_explicit_history(self):
        """批量读取保留工作区边界，空会话不隐式扩大为历史查询。"""
        directory = self.save_run('a')
        other = self.save_run('b', state='completed')
        root = self.scopes['a'].root_path
        with self.assertRaises(PermissionError):
            query_pipeline_runs(root, [str(other)], sessions=['session-a'], database_url=self.database_url)
        self.assertEqual(query_pipeline_runs(root, [str(directory)], database_url=self.database_url), [])
        self.assertEqual(len(query_pipeline_runs(root, [str(directory)], history=True,
            database_url=self.database_url)), 1)

    def test_same_path_on_two_hosts_is_ambiguous_even_in_history_mode(self):
        """不同宿主使用同一绝对目录时拒绝猜测所有权，历史模式也不能合并读取。"""
        directory = self.save_run('a')
        root = self.scopes['a'].root_path
        foreign = register_session_workspace(self.engine, host_id='other-host', session_id='session-foreign',
            owner_id='other-owner', root_path=root)
        with access_context(TrustedAccess(foreign, root)):
            documents = RunDocuments(directory)
            documents.save('state.json', {'state': 'foreign-secret', 'steps': {}})
        for query_scope in ({'sessions': ['session-a']}, {'history': True}):
            with self.subTest(query_scope=query_scope), self.assertRaises(PermissionError):
                query_pipeline_runs(root, [str(directory)], database_url=self.database_url, **query_scope)

    def test_large_documents_are_projected_in_sql_for_one_batch(self):
        """三份合法大状态超过旧缓冲上限，批次只返回阶段字段，单详情仍保留完整正文。"""
        access = self.scopes['a']
        payload = 'x' * (6 * 1024 * 1024)
        runs = []
        with access_context(access):
            for index in range(3):
                directory = Path(access.root_path) / f'operations/run-{index}/pipeline_run'
                directory.mkdir(parents=True)
                documents = RunDocuments(directory)
                documents.save('plan.json', {'inputs': {'large_input': payload}, 'steps': [
                    {'id': 'collect', 'uses': 'collection.validate', 'review_stage': 'S3',
                     'arguments': {'run_id': '$capture.run_id', 'large_input': 'irrelevant'}}]})
                documents.save('state.json', {'state': 'running', 'step': 'collect', 'steps': {
                    'collect': {'state': 'completed', 'result': {
                        'large_output': payload, 'schema': 'benchforge.collection-stage/v1',
                        'state': 'completed', 'required_items': 3,
                        'collection': {'records': 3, 'scenes': 1}, 'annotations': {'items': 3},
                        'dataset': str(directory / 'dataset'),
                        'steps': {'child': {'result': {'dataset': str(directory / 'child')}}}}}}})
                runs.append(str(directory))
        rows = query_pipeline_runs(access.root_path, runs, sessions=['session-a'],
            database_url=self.database_url, view='summary')
        self.assertEqual(len(rows), 3)
        self.assertLess(len(json.dumps(rows)), 20_000)
        for row in rows:
            self.assertNotIn('inputs', row['plan'])
            self.assertEqual(row['plan']['steps'][0]['arguments'], {'run_id': '$capture.run_id'})
            result = row['state']['steps']['collect']['result']
            self.assertNotIn('large_output', result)
            self.assertEqual(result['collection']['records'], 3)
            self.assertEqual(result['annotations']['items'], 3)
            self.assertIn('dataset', result['steps']['child']['result'])
        detail = query_pipeline_runs(access.root_path, runs[:1], sessions=['session-a'],
            database_url=self.database_url, view='detail')[0]
        self.assertEqual(detail['state']['steps']['collect']['result']['large_output'], payload)
        with self.assertRaises(ValueError):
            query_pipeline_runs(access.root_path, runs, sessions=['session-a'],
                database_url=self.database_url, view='detail')
