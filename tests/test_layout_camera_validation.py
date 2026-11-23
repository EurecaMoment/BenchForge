"""Camera calibration is rejected before expensive layout work begins."""
import unittest

from capabilities.asset_generation.layout import validate_layout


class LayoutCameraValidationTests(unittest.TestCase):
    def test_position_and_target_are_required(self):
        with self.assertRaisesRegex(ValueError, 'requires position and target'):
            validate_layout({'mode': 'generate', 'prompt': 'room', 'camera': {'position': [0, 0, 1]}})

    def test_camera_vectors_must_be_finite_and_non_degenerate(self):
        cases = (
            ({'position': [0, 0, 1], 'target': [0, 0, float('nan')]}, 'three finite numbers'),
            ({'position': [0, 0, 1], 'target': [0, 0, 1]}, 'must differ'),
            ({'position': [0, 0, 1], 'target': [0, 0, 0], 'up': [0, 0, 1]}, 'must not be parallel'),
        )
        for camera, error in cases:
            with self.subTest(camera=camera), self.assertRaisesRegex(ValueError, error):
                validate_layout({'mode': 'generate', 'prompt': 'room', 'camera': camera})

    def test_valid_camera_is_accepted(self):
        result = validate_layout({
            'mode': 'generate', 'prompt': 'room',
            'camera': {'position': [0, -4, 2], 'target': [0, 0, 1], 'up': [0, 0, 1]},
        })
        self.assertEqual(result['camera']['target'], [0, 0, 1])


if __name__ == '__main__':
    unittest.main()
