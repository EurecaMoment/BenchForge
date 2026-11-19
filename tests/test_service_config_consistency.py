"""服务、Python 客户端与 DSH 使用同一组部署配置行为样例。"""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from service_access.runtime_config import load_service_config
from service_access.scene_service import load_service_client_config


class ServiceConfigConsistencyTests(unittest.TestCase):
    def test_shared_configuration_cases(self):
        """同一输入在服务与客户端得到相同地址、凭据、模式及路径。"""
        cases = json.loads((Path(__file__).parent / 'fixtures/service_config.json').read_text(encoding='utf-8'))
        for case in cases:
            with self.subTest(case=case['name']), TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                for name, document in case['files'].items():
                    file = root / name
                    file.parent.mkdir(parents=True, exist_ok=True)
                    file.write_text(json.dumps(document), encoding='utf-8')
                with patch.dict(os.environ, case['environment'], clear=True):
                    for loader, options in ((load_service_config, {'required_tokens': ()}),
                                            (load_service_client_config, {'require_operator_token': False})):
                        if case.get('error'):
                            with self.assertRaisesRegex(ValueError, case['error']):
                                loader(root / 'client.json', **options)
                            continue
                        result = loader(root / 'client.json', **options)
                        self.assertEqual({key: result[key] for key in case['expected']}, case['expected'])
                        for key, relative in case.get('paths', {}).items():
                            self.assertEqual(Path(result[key]), root / relative)

    def test_policy_discovery_uses_environment_task_root_for_both_python_entries(self):
        """环境覆盖任务根后，两端应发现同一份场景策略。"""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            policy = root / 'tasks/scene-library/policy.json'
            policy.parent.mkdir(parents=True)
            policy.write_text('{"enabled":true,"enforce_realism":true}', encoding='utf-8')
            file = root / 'client.json'
            file.write_text('{"execution_mode":"exploration","task_root":"old-tasks"}', encoding='utf-8')
            with patch.dict(os.environ, {'BENCHFORGE_TASK_ROOT': 'tasks'}, clear=True):
                server = load_service_config(file, required_tokens=())
                client = load_service_client_config(file, require_operator_token=False)
            self.assertEqual(server['scene_policy_path'], str(policy.resolve()))
            self.assertEqual(client['scene_policy_path'], server['scene_policy_path'])
