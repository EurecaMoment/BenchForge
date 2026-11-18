import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from service_access.runtime_config import load_service_config
from service_access.configuration import selected_model, validate_providers


class RuntimeConfigTests(unittest.TestCase):
    def test_fastqwen_uses_the_shared_model_roles_without_a_fake_dspark_identity(self):
        config = {'providers': {'FastQwen': {'url':'http://127.0.0.1:8000/v1', 'model':'qwen-flash-next',
            'roles':{'scene':{'reasoning_effort':'high'}}}}}
        validate_providers(config)
        self.assertEqual(selected_model(config,'dsh')['model'],'qwen-flash-next')
        self.assertEqual(selected_model(config,'scene')['reasoning_effort'],'high')
        config['providers']['DSpark4A100']={'url':'http://127.0.0.1:9000/v1','model':'deepseek'}
        with self.assertRaisesRegex(ValueError,'one primary'):
            validate_providers(config)

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_loading_two_configs_does_not_contaminate_environment(self):
        policy = self.root / "policy.json"
        policy.write_text('{"enabled":true,"enforce_realism":true}')
        before = dict(os.environ)
        with patch.dict(
            os.environ,
            {k: v for k, v in before.items() if not k.startswith("BENCHFORGE_")},
            clear=True,
        ):
            initial = dict(os.environ)
            for mode in ("production", "exploration"):
                file = self.root / (mode + ".json")
                value = {"execution_mode": mode, "scene_policy_path": str(policy)}
                if mode == "production":
                    value["database_url"] = "postgresql+psycopg://benchforge:test@127.0.0.1/benchforge"
                file.write_text(json.dumps(value))
                self.assertEqual(
                    load_service_config(file, required_tokens=())["execution_mode"], mode
                )
            self.assertEqual(dict(os.environ), initial)
