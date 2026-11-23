"""新场景规划不能绕过图像布局证据。"""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from server.scene_requests import start_scene_request


class SceneRequestLayoutTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.store = _FakeStore(Path(temporary.name))
        self.request = {
            'request_key': 'scene-request',
            'execution_mode': 'exploration',
            'intents': [{
                'name': 'living room',
                'description': 'A living room with a sofa and table.',
                'split': 'train',
            }],
        }

    def test_text_led_scene_requires_a_layout_inventory(self):
        with self.assertRaisesRegex(ValueError, 'layout.objects'):
            start_scene_request(self.store, self.request)
        self.assertFalse(self.store.submitted)

    def test_complete_scene_program_remains_a_direct_input(self):
        result = start_scene_request(
            self.store,
            {**self.request, 'intents': [{
                **self.request['intents'][0], 'scene_program_path': 'tasks/scene.json',
            }]},
            submission_reader=lambda *_args, **_kwargs: {'program': {'schema': 'test'}},
            prior_reference_checker=lambda *_args, **_kwargs: None,
        )

        self.assertEqual(result['state'], 'accepted')
        self.assertEqual(self.store.submitted[0][1]['submitted_programs'], [{'program': {'schema': 'test'}}])


class _FakeStore:
    def __init__(self, root):
        self.root = root
        self.submitted = []

    def submit(self, *args, **kwargs):
        self.submitted.append((args, kwargs))
        return 'sf_0123456789abcdef'


if __name__ == '__main__':
    unittest.main()
