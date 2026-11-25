from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.database_fixtures import use_postgres
from unittest.mock import patch

from sqlalchemy import Column, JSON, MetaData, String, Table, inspect

import deployment_config
from service_access.configuration import isaac_config, isaac_endpoint
from capabilities.isaac_sim.recovery import prepare_retry
from service_access.config_migration import migrate_configuration
from storage.files import write_json
from storage.scene_jobs.metadata import tasks
from storage.scene_jobs.migrate_capture import migrate_capture
from pipelines.scene_production.service import create_store


class CaptureStoreTests(unittest.TestCase):
    # 建立探索模式下的独立场景存储及临时目录，隔离采集迁移测试。
    def setUp(self):
        self.database_url = use_postgres(self)
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.env = patch.dict(os.environ, {'BENCHFORGE_EXECUTION_MODE': 'exploration'})
        self.env.start()
        self.store = create_store(self.root / 'artifacts', database_url=self.database_url)

    # 释放数据库引擎、恢复执行环境并删除临时采集目录。
    def tearDown(self):
        self.store.repo.engine.dispose()
        self.env.stop()
        self.temporary.cleanup()

    # 创建并推进一个待采集工作项，返回其编号以构造不同旧协议状态。
    def capture(self, key):
        run = self.store.submit(key, [{'name': key, 'execution_mode': 'exploration'}])
        task = self.store.snapshot(run)['tasks'][0]
        self.store.update(task['id'], state='READY', unit={**task['unit'], 'phase': 'capture'})
        return task['id']

    # 验证迁移保留历史产物、只执行一次，并要求显式重试旧协议采集。
    def test_migration_preserves_artifacts_and_requires_explicit_retry_once(self):
        task = self.capture('legacy')
        directory = self.store.directory(task, 0)
        write_json(directory / 'program.json', {'original': True})
        write_json(directory / 'capture/report.json', {'token': 'legacy-token', 'status': 'captured'})
        devices = Table('benchforge_devices', MetaData(), Column('id', String, primary_key=True), Column('value', JSON))
        with self.store.repo.transaction() as connection:
            devices.create(connection)
            connection.execute(devices.insert().values(id='desktop-isaac', value={'active_task': task}))
        result = migrate_capture(self.store)
        self.assertEqual(result['retired_tasks'], 1)
        backup = json.loads(Path(result['backup_path']).read_text())
        self.assertEqual(backup['devices'][0]['id'], 'desktop-isaac')
        self.assertEqual(backup['tasks'][0]['id'], task)
        self.assertFalse(inspect(self.store.repo.engine).has_table('benchforge_devices'))
        record = self.store.snapshot(task.split('.')[0])['tasks'][0]
        self.assertEqual(record['state'], 'FAILED_FINAL')
        self.assertEqual(record['failure']['code'], 'CAPTURE_PROTOCOL_MIGRATED')
        self.assertTrue((directory / 'program.json').exists())
        self.assertTrue(migrate_capture(self.store)['already_applied'])
        with self.assertRaisesRegex(ValueError, 'retired protocol'):
            self.store.retry(task, capture_mode='resume')
        retry = self.store.retry(task)
        self.assertEqual(retry['capture_recovery_mode'], 'restart')
        self.assertEqual(retry['revision'], 1)
        self.assertEqual(json.loads((directory / 'capture/report.json').read_text())['token'], 'legacy-token')
        self.assertEqual(json.loads((self.store.directory(task, 1) / 'program.json').read_text()), {'original': True})
        self.assertFalse((self.store.directory(task, 1) / 'capture').exists())
        self.assertIsNotNone(self.store.claim_stage(task, 'after-migration'))

    # 验证已使用当前原生协议的采集回执不被迁移误作废。
    def test_existing_native_receipt_is_not_retired(self):
        task = self.capture('native')
        write_json(self.store.directory(task, 0) / 'isaac_sim/attempt_0/capture.json', {'protocol': 'a100sim.v1.Simulator'})
        self.assertEqual(migrate_capture(self.store)['retired_tasks'], 0)
        self.assertEqual(self.store.snapshot(task.split('.')[0])['tasks'][0]['state'], 'READY')

    # 验证不同采集任务可独立领取，而过期所有者不能发布产物。
    def test_capture_claims_are_independent_and_publication_rejects_stale_owner(self):
        first, second = self.capture('first'), self.capture('second')
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda item: self.store.claim_stage(*item), [(first, 'first-owner'), (second, 'second-owner')]))
        self.assertTrue(all(claims))
        self.assertIsNone(self.store.claim_stage(first, 'intruder'))
        with self.store.repo.transaction() as connection:
            connection.execute(tasks.update().where(tasks.c.id == first).values(lease_expires_at=0))
        self.assertIsNotNone(self.store.claim_stage(first, 'new-owner'))
        incoming = self.root / 'incoming'
        write_json(incoming / 'report.json', {'token': 'receipt', 'status': 'captured'})
        with self.assertRaisesRegex(ValueError, 'lease changed'):
            self.store.publish_capture(first, 'receipt', incoming, expected_owner='first-owner')
        self.assertTrue(incoming.exists())
        self.store.publish_capture(first, 'receipt', incoming, expected_owner='new-owner')
        record = self.store.snapshot(first.split('.')[0])['tasks'][0]
        self.assertEqual((record['state'], record['unit']['phase']), ('READY', 'review'))
        self.assertTrue((self.store.directory(first, 0) / 'capture/report.json').exists())
        self.assertTrue(self.store.renew_stage(second, 'second-owner'))

    # 验证恢复过程复用已知活动远端任务，避免重复提交采集。
    def test_recovery_does_not_duplicate_known_active_native_jobs(self):
        directory = self.root / 'revision'
        receipt = directory / 'isaac_sim/attempt_0/jobs/capture/receipt.json'
        write_json(receipt, {'job_id': 'job-existing', 'state': 'RUNNING'})
        self.assertEqual(prepare_retry(directory, {}, 'auto')['capture_generation'], 0)
        with self.assertRaisesRegex(ValueError, 'Resume observation'):
            prepare_retry(directory, {}, 'restart')
        write_json(receipt, {'job_id': 'job-existing', 'state': 'INTERRUPTED'})
        self.assertEqual(prepare_retry(directory, {}, 'auto')['capture_generation'], 1)
        with self.assertRaisesRegex(ValueError, 'terminal'):
            prepare_retry(directory, {}, 'resume')


class ConfigurationMigrationTests(unittest.TestCase):
    # 验证配置迁移保留完整模型端点、推理与上下文设置。
    def test_migration_preserves_full_model_settings(self):
        models = {'url': 'http://127.0.0.1:8000/v1', 'model': 'existing',
            'context_window': 1048576, 'max_tokens': 65536, 'reasoning_effort': 'max',
            'input': ['text', 'image']}
        source = {'config_version': 4, 'providers': {
            'DSpark4A100': models, 'IssacSim4A100': {'mode': 'grpc',
                'grpc': {'host': '127.0.0.1', 'port': 50051}, 'capture_timeout_seconds': 1800}},
            'worker': {'capture': {'width': 768}}, 'server': {'worker_token': 'retired', 'operator_token': 'retained'}}
        frozen = deepcopy(source)
        result = migrate_configuration(source)
        self.assertEqual(source, frozen)
        self.assertEqual(result['providers']['DSpark4A100'], models)
        self.assertNotIn('worker', result)
        self.assertNotIn('IssacSim4A100', result['providers'])
        self.assertEqual(result['server'], {'operator_token': 'retained'})
        self.assertEqual(migrate_configuration(result), result)

    def test_sync_projects_isaac_capture_provider_to_server_config(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, generated, state = root / 'config', root / 'config/generated', root / 'state'
            example = Path(__file__).resolve().parents[1] / 'config/benchforge.example.json'
            write_json(config / 'benchforge.example.json', json.loads(example.read_text()))
            binding = {'asset_id': 'native-office-001', 'relative_path': 'Props/SM_Sofa.usd'}
            write_json(config / 'benchforge.local.json', {
                'config_version': 5,
                'providers': {'IsaacSim4A100': {
                    'grpc': {'host': '127.0.0.1', 'port': 25052, 'timeout_seconds': 60},
                    'asset_sources': {'office_sofa': binding},
                }},
                'server': {'execution_mode': 'exploration'},
            })

            deployment_config.sync_configuration(
                config, generated, generated / deployment_config.SERVER_CONFIG, state)
            runtime = json.loads((generated / deployment_config.SERVER_CONFIG).read_text())
            settings = isaac_config(runtime)

            self.assertEqual(isaac_endpoint(settings['grpc']), '127.0.0.1:25052')
            self.assertEqual(settings['asset_sources']['office_sofa'], binding)

    # 验证配置同步保留退役配置备份和既有凭据。
    def test_sync_backs_up_retired_config_and_keeps_credentials(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, generated, state = root / 'config', root / 'config/generated', root / 'state'
            example = Path(__file__).resolve().parents[1] / 'config/benchforge.example.json'
            write_json(config / 'benchforge.example.json', json.loads(example.read_text()))
            write_json(config / 'benchforge.local.json', {'config_version': 4,
                'providers': {'IssacSim4A100': {'mode': 'grpc', 'grpc': {'host': 'localhost'}}}})
            write_json(state / 'credentials.local.json', {'operator_token': 'same-operator',
                'worker_token': 'retired', 'database_password': 'same-database'})
            write_json(generated / 'worker.local.json', {'worker_token': 'retired'})
            deployment_config.sync_configuration(config, generated, generated / deployment_config.SERVER_CONFIG, state)
            self.assertFalse((generated / 'worker.local.json').exists())
            self.assertTrue((state / 'retired-capture-config/worker.local.json.json').exists())
            self.assertEqual(json.loads((state / 'configuration-before-v5.json').read_text())['config_version'], 4)
            credentials = json.loads((state / 'credentials.local.json').read_text())
            self.assertEqual({key: credentials[key] for key in ('operator_token', 'database_password')},
                             {'operator_token': 'same-operator', 'database_password': 'same-database'})
            self.assertNotIn('worker_token', credentials)
            self.assertTrue(credentials['database_reader_role'].startswith('benchforge_reader_'))
            self.assertNotEqual(credentials['database_reader_password'], credentials['database_password'])
            self.assertTrue(credentials['database_reader_password'])
            for file in (config / 'benchforge.local.json', generated / deployment_config.SERVER_CONFIG,
                         generated / deployment_config.CLIENT_CONFIG):
                public_config = file.read_text()
                self.assertNotIn(credentials['database_reader_password'], public_config)
                self.assertNotIn(credentials['database_reader_role'], public_config)
            deployment_config.sync_configuration(config, generated, generated / deployment_config.SERVER_CONFIG, state)
            self.assertEqual(json.loads((state / 'credentials.local.json').read_text()), credentials)
            self.assertNotIn('worker_token', json.loads((generated / deployment_config.SERVER_CONFIG).read_text()))


if __name__ == '__main__':
    unittest.main()
