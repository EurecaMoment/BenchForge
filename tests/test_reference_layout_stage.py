"""Regression coverage for recoverable image-layout stages."""
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from capabilities.asset_generation.reference_layout_stage import (
    _select_detections,
    prepare_reference_layout,
)


class ReferenceLayoutStageTests(unittest.TestCase):
    def test_same_prompt_does_not_assign_one_detection_to_two_objects(self):
        detections = [
            {'text_prompt': 'chair', 'box': [0, 0, 20, 20], 'score': .9, 'mask': 'first.png'},
            {'text_prompt': 'chair', 'box': [30, 30, 50, 50], 'score': .8, 'mask': 'second.png'},
        ]

        first = _select_detections(detections, 'chair', 1)
        second = _select_detections(
            detections, 'chair', 1, first)

        self.assertEqual(first[0]['mask'], 'first.png')
        self.assertEqual(second[0]['mask'], 'second.png')

    def test_partial_estimate_retries_failed_trellis_asset(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_path = root / 'reference.png'
            Image.new('RGB', (8, 8), 'white').save(image_path)
            segmentation = root / 'reference_layout' / 'segmentation'
            segmentation.mkdir(parents=True)
            mask_path = segmentation / 'chair.png'
            mask = np.zeros((8, 8), dtype=np.uint8)
            mask[1:7, 1:7] = 255
            Image.fromarray(mask).save(mask_path)
            depth_path = root / 'depth.npy'
            np.save(depth_path, np.full((8, 8), 2, dtype=np.float32))

            sam_result = {'segments': [{
                'text_prompt': 'chair', 'box': [1, 1, 7, 7], 'score': .9,
                'mask': 'chair.png',
            }]}
            depth_result = {
                'depth': str(depth_path), 'depth_type': 'estimated_metric_depth', 'units': 'm',
            }

            class _Tools:
                attempts = 0

                def generate_asset(self, *_args, **_kwargs):
                    self.attempts += 1
                    if self.attempts == 1:
                        raise RuntimeError('temporary TRELLIS failure')
                    return {'asset_id': 'chair_mesh', 'geometry': {
                        'bounds': [[-.5, -.5, -.5], [.5, .5, .5]],
                    }}

            tools = _Tools()
            spec = {
                'seed': 7,
                'objects': [{'object_id': 'chair', 'label': 'chair',
                             'segmentation_prompt': 'chair', 'count': 1,
                             'representation': 'auto', 'requires_articulation': False}],
            }
            with patch(
                'capabilities.asset_generation.reference_layout_stage._run_image_analysis',
                return_value=(sam_result, depth_result, segmentation),
            ) as analyze:
                first = prepare_reference_layout(tools, image_path, spec, root, threading.Event())
                second = prepare_reference_layout(tools, image_path, spec, root, threading.Event())

            self.assertEqual(first['status'], 'partial')
            self.assertEqual(second['status'], 'ready')
            self.assertEqual(tools.attempts, 2)
            self.assertEqual(analyze.call_count, 2)
            self.assertEqual(second['objects'][0]['mesh_asset_id'], 'chair_mesh')


if __name__ == '__main__':
    unittest.main()
