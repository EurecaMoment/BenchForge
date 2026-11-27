"""Only current-program layouts may appear before a real scene image is available."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from capabilities.scene_library import observations


class SceneLayoutPreviewTests(unittest.TestCase):
    def test_retained_preview_publishes_only_versioned_job_owned_png(self):
        from PIL import Image
        from capabilities.isaac_sim import interactive_preview

        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            source = directory / 'source.png'
            Image.new('RGB', (64, 64), 'green').save(source)
            settings = {'size': 64, 'spp': 1}
            with (patch.object(interactive_preview, 'A100Client') as connection,
                  patch.object(interactive_preview, 'DurableTasks') as tasks):
                jobs = tasks.return_value
                jobs.extension.return_value = ('job', {'image': 'artifact://rgb', 'scene_version': 'revision-2'})
                jobs.download.return_value = [{'artifact_id': 'rgb', 'source_path': str(source)}]
                interactive_preview.fetch_preview({}, settings, 'room', 'camera', directory, 2)
                image = directory / interactive_preview.preview_name(2)
                self.assertEqual(image.read_bytes(), source.read_bytes())
                jobs.extension.assert_called_once_with('preview', 'scene.preview',
                    {'session_id': 'room', 'camera': 'camera', 'size': 64, 'spp': 1})
                connection.return_value.close.assert_called_once()
                jobs.extension.return_value = ('other-job', {'image': 'artifact://rgb'})
                with self.assertRaisesRegex(ValueError, 'version'):
                    interactive_preview.fetch_preview({}, settings, 'room', 'camera', directory, 3)
                self.assertFalse((directory / interactive_preview.preview_name(3)).exists())

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config = {'artifacts': str(self.root)}
        self.scene = 'sf_layouttest'
        self.task = {'id': self.scene + '.scene0', 'state': 'PENDING', 'unit': {'revision': 0, 'workflow': 'scene',
                     'intent': {}, 'submitted_program': {'program': {'title': 'Current layout', 'objects': [
                         {'id': 'table', 'label': 'Table', 'xy': [3, 4], 'size': [4, 2, 1], 'yaw_deg': 90}]}}}}

    def query(self, evidence):
        response = {'tasks': [self.task], 'state': self.task['state']}
        with patch.object(observations, 'service_read', return_value=response), \
             patch.object(observations, 'optional_evidence', side_effect=lambda _, args: evidence.get(args['file'], {})):
            return observations.stage_artifacts(self.config, 'session-test', self.scene, run_ids=[self.scene])

    def test_queued_scene_has_a_layout_without_creating_a_capture(self):
        result = self.query({})
        self.assertEqual(result['items'], [])
        layout, = result['layouts']
        self.assertEqual((layout['task_id'], layout['revision']), (self.task['id'], 0))
        self.assertEqual(layout['footprints'][0]['points'], [[4, -2], [4, -6], [2, -6], [2, -2]])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_prepared_program_replaces_the_submitted_layout_in_same_revision(self):
        folder = self.root / self.task['id'] / 'revision_0'
        folder.mkdir(parents=True)
        (folder / 'program.json').write_text('{"title":"Prepared","objects":[{"id":"ball","xy":[0,1],"size":[2,2,2]}]}')
        self.assertEqual(self.query({})['layouts'][0]['name'], 'Prepared')

    def test_retry_uses_only_the_current_revision(self):
        self.task['unit']['revision'] = 1
        self.assertEqual(self.query({})['layouts'], [])
        folder = self.root / self.task['id'] / 'revision_1'
        folder.mkdir(parents=True)
        (folder / 'program.json').write_text('{"title":"Revised","objects":[{"id":"ball","xy":[0,1],"size":[2,2,2]}]}')
        self.assertEqual(self.query({})['layouts'][0]['name'], 'Revised')

    def test_rendered_image_replaces_layout(self):
        folder = self.root / self.task['id'] / 'revision_0'
        (folder / 'isaac_sim' / 'attempt_0').mkdir(parents=True)
        preview = folder / 'preview' / 'attempt_0' / 'first_view.png'
        preview.parent.mkdir(parents=True)
        preview.write_bytes(b'available')
        result = self.query({})
        self.assertEqual(result['layouts'], [])
        self.assertEqual(result['items'][0]['purpose'], 'interactive_preview')
        result = self.query({'capture': {'files': [{'file': 'capture/view_0.png'}]}})
        self.assertEqual(result['layouts'], [])
        self.assertEqual([row['file'] for row in result['items']], ['capture/view_0.png'])
