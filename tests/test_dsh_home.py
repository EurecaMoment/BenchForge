"""The DSH home must not live inside the selected repository workspace."""
import importlib.util
import os
from pathlib import Path
import unittest
from unittest.mock import patch


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location('benchforge_launcher', ROOT / 'benchforge.py')
LAUNCHER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LAUNCHER)


class DshHomeTests(unittest.TestCase):
    def test_default_home_is_a_sibling_of_the_repository(self):
        with patch.dict(os.environ, {}, clear=True):
            home = LAUNCHER.configured_dsh_home()
        self.assertEqual(home, LAUNCHER.ROOT.parent / f'{LAUNCHER.ROOT.name}-dsh-home')
        self.assertNotIn(LAUNCHER.ROOT, home.parents)

    def test_environment_override_is_resolved(self):
        requested = ROOT / 'external-dsh-state'
        with patch.dict(os.environ, {'BENCHFORGE_DSH_HOME': str(requested)}, clear=True):
            self.assertEqual(LAUNCHER.configured_dsh_home(), requested.resolve())


if __name__ == '__main__':
    unittest.main()
