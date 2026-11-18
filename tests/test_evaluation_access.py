"""Evaluation consumes declared files within the caller's verified workspace."""

import base64
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from capabilities.evaluation.benchmark import reports
from database.access import AccessScope
from service_access.access_scope import TrustedAccess, access_context
from storage.artifacts import jsonl, read, rows, write


class EvaluationAccessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / 'workspace'
        self.workspace.mkdir()
        self.output = self.root / 'private-service-output'
        self.output.mkdir()
        self.access = TrustedAccess(AccessScope('host', 'session', 'workspace', 'owner', 1, 1),
                                    str(self.workspace))
        self.items = self.workspace / 'items.jsonl'
        jsonl(self.items, [{'id': 'q1', 'question': 'Choose one', 'answer': 'A',
                            'answer_type': 'choice', 'choices': {'A': 'first'}, 'media': []}])
        self.config = {'models': {'fixture': {'url': 'http://127.0.0.1:9000/v1',
                                             'model': 'fixture'}}}

    def media_items(self, path):
        items = rows(self.items)
        items[0]['media'] = [str(path)]
        jsonl(self.items, items)

    def test_package_keeps_provenance_strings_inert(self):
        secret = self.root / 'private.txt'
        secret.write_text('private-credential-value', encoding='utf-8')
        bundle = self.workspace / 'bundle'
        bundle.mkdir()
        write(bundle / 'manifest.json', {'note': str(secret), 'provenance': {'path': str(secret),
                                                                              'image': str(secret)},
                                         'command': [str(secret)]})
        result = reports.package({'items': str(self.items), 'bundle': str(bundle)}, self.output, {})
        record = read(self.output / 'benchmark/reproduce/manifest.json')
        self.assertEqual(record['note'], str(secret))
        self.assertEqual(list((self.output / 'benchmark/reproduce/inputs').iterdir()), [])
        with zipfile.ZipFile(result['complete_package']) as archive:
            self.assertFalse(any(b'private-credential-value' in archive.read(name)
                                 for name in archive.namelist()))

    def test_unscoped_package_copies_declared_resources_and_keeps_public_private_split(self):
        image = self.workspace / 'camera.png'
        image.write_bytes(b'public-image')
        self.media_items(image)
        bundle = self.workspace / 'bundle'
        bundle.mkdir()
        write(bundle / 'manifest.json', {'media': [str(image)],
                                         'resources': [{'path': str(image)}]})
        result = reports.package({'items': str(self.items), 'bundle': str(bundle)}, self.output, {})
        record = read(self.output / 'benchmark/reproduce/manifest.json')
        self.assertEqual(record['media'], ['inputs/input_000000.png'])
        self.assertEqual(record['resources'], [{'path': 'inputs/input_000000.png'}])
        with zipfile.ZipFile(result['public_package']) as archive:
            public = json.loads(archive.read('items.jsonl'))
            self.assertNotIn('answer', public)
            self.assertEqual(archive.read(public['media'][0]), b'public-image')
        jsonl(self.root / 'predictions.jsonl', [{'id': 'q1', 'answer': 'A'}])
        benchmark = self.output / 'benchmark'
        runner = (f"import runpy,sys;sys.path.insert(0,{str(benchmark)!r});"
                  f"runpy.run_path({str(benchmark / 'score.py')!r},run_name='__main__')")
        completed = subprocess.run([
            sys.executable, '-I', '-B', '-c', runner,
            '--items', str(self.output / 'benchmark/authority/items.jsonl'),
            '--predictions', str(self.root / 'predictions.jsonl'),
            '--out', str(self.root / 'metrics.json'),
        ], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(read(self.root / 'metrics.json')['overall']['score'], 1)

    def test_scoped_primary_input_outside_workspace_is_rejected(self):
        outside = self.root / 'outside.jsonl'
        jsonl(outside, rows(self.items))
        with access_context(self.access), self.assertRaises(PermissionError):
            reports.model_eval({'public_items': str(outside)}, self.output, self.config)

    def test_scoped_external_media_rejected_before_network(self):
        secret = self.root / 'private.png'
        secret.write_bytes(b'private')
        self.media_items(secret)
        # The record parser is covered by descriptor tests on Linux. Bypass it
        # here so the authorization assertion also runs on Windows development.
        with access_context(self.access), patch.object(reports, '_input_rows', return_value=rows(self.items)), \
                patch('urllib.request.build_opener') as network, self.assertRaises(PermissionError):
            reports.model_eval({'public_items': str(self.items)}, self.output, self.config)
        network.assert_not_called()

    def test_bundle_rejects_linked_files_and_directories(self):
        secret = self.root / 'private.png'
        secret.write_bytes(b'private')
        for directory in (False, True):
            bundle = self.workspace / ('bundle-dir' if directory else 'bundle-file')
            bundle.mkdir()
            link = bundle / 'linked'
            try:
                link.symlink_to(self.output if directory else secret, target_is_directory=directory)
            except OSError as exc:
                self.skipTest('Creating symlinks is unavailable: ' + str(exc))
            with self.assertRaises(PermissionError):
                reports._reproduce_bundle(bundle, self.output / bundle.name)

    def test_zip_rejects_symlinks(self):
        link = self.workspace / 'linked'
        try:
            link.symlink_to(self.items)
        except OSError as exc:
            self.skipTest('Creating symlinks is unavailable: ' + str(exc))
        with self.assertRaises(PermissionError):
            reports._zip_tree(self.workspace, self.output / 'archive.zip')

    @unittest.skipUnless(os.name == 'posix', 'Descriptor-relative reads require Linux')
    def test_scoped_package_rejects_declared_external_resource(self):
        secret = self.root / 'private.png'
        secret.write_bytes(b'private')
        bundle = self.workspace / 'bundle'
        bundle.mkdir()
        write(bundle / 'manifest.json', {'resources': [{'path': str(secret)}]})
        with access_context(self.access), self.assertRaises(PermissionError):
            reports.package({'items': str(self.items), 'bundle': str(bundle)}, self.output, {})
        self.assertFalse((self.output / 'benchmark-complete.zip').exists())

    @unittest.skipUnless(os.name == 'posix', 'Descriptor-relative reads require Linux')
    def test_scoped_model_eval_reads_in_workspace_media(self):
        image = self.workspace / 'camera.png'
        image.write_bytes(b'public-image')
        self.media_items('camera.png')
        response = BytesIO(json.dumps({'choices': [{'message': {'content': 'A'}}]}).encode())
        with access_context(self.access), patch('urllib.request.build_opener') as factory:
            factory.return_value.open.return_value = response
            result = reports.model_eval({'public_items': 'items.jsonl'}, self.output, self.config)
        request = factory.return_value.open.call_args.args[0]
        sent = json.loads(request.data)
        content = sent['messages'][0]['content']
        self.assertEqual(content[1]['image_url']['url'],
                         'data:image/png;base64,' + base64.b64encode(b'public-image').decode())
        self.assertEqual(result['status'], 'completed')

    @unittest.skipUnless(os.name == 'posix', 'Descriptor-relative reads require Linux')
    def test_scoped_package_rejects_media_symlink(self):
        target = self.workspace / 'camera.png'
        target.write_bytes(b'image')
        link = self.workspace / 'link.png'
        link.symlink_to(target)
        self.media_items(link)
        with access_context(self.access), self.assertRaises(PermissionError):
            reports.package({'items': str(self.items)}, self.output, {})

    @unittest.skipUnless(os.name == 'posix', 'Descriptor-relative reads require Linux')
    def test_scoped_package_normalizes_internal_references(self):
        bundle = self.workspace / 'bundle'
        bundle.mkdir()
        (bundle / 'camera.png').write_bytes(b'image')
        write(bundle / 'manifest.json', {'media': ['bundle/camera.png'],
                                         'image_path': str(bundle / 'camera.png')})
        with access_context(self.access):
            reports.package({'items': 'items.jsonl', 'bundle': 'bundle'}, self.output, {})
        record = read(self.output / 'benchmark/reproduce/manifest.json')
        self.assertEqual(record, {'media': ['camera.png'], 'image_path': 'camera.png'})

    @unittest.skipUnless(os.name == 'posix', 'Descriptor-relative reads require Linux')
    def test_media_replacement_after_path_validation_cannot_escape(self):
        from service_access.access_scope import workspace_path
        image = self.workspace / 'camera.png'
        image.write_bytes(b'image')
        secret = self.root / 'secret.png'
        secret.write_bytes(b'private')

        def replace_after_validation(value, **kwargs):
            path = workspace_path(value, **kwargs)
            if path == image:
                image.unlink()
                image.symlink_to(secret)
            return path

        with access_context(self.access), \
                patch('service_access.access_scope.workspace_path', side_effect=replace_after_validation), \
                self.assertRaises(OSError):
            reports._input_bytes(image)

    @unittest.skipUnless(os.name == 'posix', 'Descriptor-relative reads require Linux')
    def test_zip_does_not_follow_a_file_replaced_after_enumeration(self):
        image = self.workspace / 'camera.png'
        image.write_bytes(b'image')
        secret = self.root / 'secret.png'
        secret.write_bytes(b'private')

        def replaced_files(_root):
            image.unlink()
            image.symlink_to(secret)
            yield image

        with patch.object(reports, '_bundle_files', side_effect=replaced_files), self.assertRaises(OSError):
            reports._zip_tree(self.workspace, self.output / 'archive.zip')


if __name__ == '__main__':
    unittest.main()
