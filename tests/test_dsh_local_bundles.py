"""Verify the startup gate rejects stale or partially rebuilt checkout bundles."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from dsh.local_bundles import _STAGED_OUTPUTS, ensure_local_bundles, local_bundle_entries


class LocalBundleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.dsh = self.root / 'src/dsh'
        self.entry = self.dsh / 'dist/scene-tools.mjs'
        self.stamp = self.root / '.benchforge/dsh-bundles.json'
        self.dsh.mkdir(parents=True)
        self.source = self.dsh / 'build.mjs'
        self.source.write_text('original', encoding='utf-8')
        (self.dsh / 'package.json').write_text(json.dumps({
            'exports': {'.': './dist/scene-tools.mjs'},
        }), encoding='utf-8')
        support = self.dsh.parent / 'capabilities/data_acquisition/measurements'
        support.mkdir(parents=True)
        for output in _STAGED_OUTPUTS:
            if output.startswith('dist/task_support/'):
                (support / Path(output).name).write_text('staged input', encoding='utf-8')

    def build(self, *args, **kwargs):
        for target in ('dist/scene-tools.mjs', 'dist/workbench.mjs', 'client/client.js', *_STAGED_OUTPUTS):
            output = self.dsh / target
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text('built', encoding='utf-8')

    def test_only_enabled_checkout_entries_are_selected(self):
        preset = self.root / 'config' / 'preset.yml'
        other = (self.root / 'external' / 'dist/scene-tools.mjs').as_uri()
        profile = [
            {'name': '@eurecamoment/benchforge-dsh/benchforge-tools'},
            {'name': './client/index.js'},
            {'name': other},
            {'disabled': 'True', 'config': {'plugins': [{'name': self.entry.as_uri()}]}},
            {'insert': [
                {'name': '../src/dsh/client/index.js'},
                {'name': self.entry.as_uri()},
            ]},
        ]
        self.assertEqual(local_bundle_entries(profile, preset, self.dsh),
                         {self.entry, self.dsh / 'client/index.js'})

    def test_rebuilds_after_source_changes_and_missing_output(self):
        with patch('dsh.local_bundles.subprocess.run', side_effect=self.build) as build:
            ensure_local_bundles(self.dsh, 'node', {self.entry})
            ensure_local_bundles(self.dsh, 'node', {self.dsh / 'dist/workbench.mjs'})
            self.assertEqual(build.call_count, 1)

            self.source.write_text('updated', encoding='utf-8')
            ensure_local_bundles(self.dsh, 'node', {self.entry})
            self.assertEqual(build.call_count, 2)

            staged = self.dsh / 'dist/resources/assessment_template.json'
            staged.unlink()
            ensure_local_bundles(self.dsh, 'node', {self.entry})
            self.assertEqual(build.call_count, 3)
            self.assertTrue(staged.is_file())

    def test_failed_build_invalidates_stamp_and_next_attempt_rebuilds(self):
        with patch('dsh.local_bundles.subprocess.run', side_effect=self.build):
            ensure_local_bundles(self.dsh, 'node', {self.entry})
        self.assertTrue(self.stamp.is_file())
        self.source.write_text('updated', encoding='utf-8')

        def fail_build(*args, **kwargs):
            self.entry.write_text('partial', encoding='utf-8')
            raise subprocess.CalledProcessError(1, ['node', 'build.mjs'])

        with patch('dsh.local_bundles.subprocess.run', side_effect=fail_build):
            with self.assertRaisesRegex(RuntimeError, 'DSH bundle build failed'):
                ensure_local_bundles(self.dsh, 'node', {self.entry})
        self.assertFalse(self.stamp.exists())

        with patch('dsh.local_bundles.subprocess.run', side_effect=self.build) as build:
            ensure_local_bundles(self.dsh, 'node', {self.entry})
        build.assert_called_once()
        self.assertEqual(self.entry.read_text(encoding='utf-8'), 'built')
