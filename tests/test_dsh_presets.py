"""Persona composition and installation must work without deployment workarounds."""
from copy import deepcopy
from pathlib import Path
import re
import tempfile
import unittest

from dsh.presets import build_preset as base
from dsh.presets.build_presets import build
from dsh.presets.profile_migration import migrate_profile


def standard():
    return [{'insert': [{'id': 'preset-standard', 'name': '@deepseek-ai/dsh-agent-preset',
                        'config': {'id': 'standard', 'plugins': [
                            {'id': 'persona', 'name': '@deepseek-ai/dsh-persona',
                             'config': {'prefix': 'Model {{model}}'}},
                        ]}}]}]


def plugins(profile):
    return next(row['config']['plugins'] for patch in profile for row in patch.get('insert', [])
                if row.get('id') == 'preset-benchforge')


def persona(profile):
    return next(row['config'] for row in plugins(profile) if row['id'] == 'persona')


class DshPresetTests(unittest.TestCase):
    def generated(self, portable=False):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'missing-config.json'
            return build(standard(), 'python3', config, config, package_imports=portable)

    def assert_renderable(self, profile):
        values = {'provider': 'test-provider', 'model': 'test-model'}
        for field in ('prefix', 'suffix'):
            template = persona(profile).get(field, '')
            # Match DSH's strict default-variable contract: unknown names fail.
            rendered = re.sub(r'{{\s*([a-z][a-z0-9_]*)\s*}}',
                              lambda match: values[match[1]], template)
            self.assertNotRegex(rendered, r'{{[^{}]+}}')

    def test_local_and_portable_presets_need_only_builtin_prompt_variables(self):
        for portable in (False, True):
            with self.subTest(portable=portable):
                self.assert_renderable(self.generated(portable))

    def test_published_bundle_needs_only_builtin_prompt_variables(self):
        file = Path(__file__).parents[1] / 'src/dsh/cordis.patch.yml'
        self.assert_renderable(base.yaml.load(file.read_text(encoding='utf-8'), Loader=base.Loader))

    def test_upgrade_repairs_generated_suffix_and_preserves_local_settings(self):
        generated = self.generated()
        old = deepcopy(generated)
        persona(old)['suffix'] = 'Your working directory is {{cwd}}. You are using {{model}}.'
        extra = {'id': 'local-grpc', 'name': 'file:///instance/grpc.mjs', 'config': {'port': 50051}}
        plugins(old).append(extra)
        limits = {'id': 'attachment-local', 'config': {'maxImages': 1000}}
        old.extend([limits, {'id': 'hmr', 'disabled': True}])
        updated = migrate_profile(old, generated)
        self.assert_renderable(updated)
        self.assertIn(extra, plugins(updated))
        self.assertIn(limits, updated)
        self.assertIn({'id': 'hmr', 'disabled': True}, updated)

    def test_upgrade_preserves_operator_persona_suffix(self):
        generated = self.generated()
        old = deepcopy(generated)
        custom = 'Deployment {{custom_context}}; use {{model}}.'
        persona(old)['suffix'] = custom
        self.assertEqual(persona(migrate_profile(old, generated))['suffix'], custom)


if __name__ == '__main__':
    unittest.main()
