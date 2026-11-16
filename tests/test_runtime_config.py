import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from service_access.runtime_config import load_service_config


class RuntimeConfigTests(unittest.TestCase):
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
                file.write_text(
                    json.dumps({"execution_mode": mode, "scene_policy_path": str(policy)})
                )
                self.assertEqual(
                    load_service_config(file, required_tokens=())["execution_mode"], mode
                )
            self.assertEqual(dict(os.environ), initial)
