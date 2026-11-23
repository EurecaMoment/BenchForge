import unittest

import numpy as np

from capabilities.scene_authoring.geometry.reference_layout import (
    _mesh_fit,
    analyze_reference_layout,
    scene_camera_calibration,
)


class ReferenceLayoutTests(unittest.TestCase):
    def setUp(self):
        self.image_size = [100, 80]
        self.intrinsics = [[100, 0, 50], [0, 100, 40], [0, 0, 1]]
        self.camera_to_world = [
            [1, 0, 0, 0],
            [0, 0, 1, 0],
            [0, -1, 0, 2],
            [0, 0, 0, 1],
        ]

    @staticmethod
    def mask(x0, y0, x1, y1):
        value = np.zeros((80, 100), dtype=np.uint8)
        value[y0:y1, x0:x1] = 255
        return value

    def test_metric_depth_projects_positions_and_trellis_mesh_to_scene_fields(self):
        depth = np.full((80, 100), 2.0, dtype=np.float32)
        bounds = [[-.5, -.25, -.5], [.5, .25, .5]]
        result = analyze_reference_layout(
            depth,
            [{'object_id': 'chair', 'label': 'chair', 'mask': self.mask(40, 30, 60, 50),
              'mesh_bounds': bounds}],
            self.image_size, depth_type='metric_depth_estimate', units='meter',
            intrinsics_px=self.intrinsics, camera_to_world=self.camera_to_world)

        item = result['objects'][0]
        self.assertAlmostEqual(item['camera_position_m'][0], -.01)
        self.assertAlmostEqual(item['camera_position_m'][1], -.01)
        self.assertEqual(item['camera_position_m'][2], 2)
        self.assertAlmostEqual(item['world_position_m'][0], -.01)
        self.assertAlmostEqual(item['world_position_m'][1], 2)
        self.assertAlmostEqual(item['world_position_m'][2], 2.01)
        self.assertEqual(item['projected_size_m']['width_m'], .4)
        self.assertEqual(item['projected_size_m']['height_m'], .4)
        fit = item['trellis_fit']
        self.assertAlmostEqual(fit['uniform_scale_m_per_source_unit'], .4)
        fields = fit['scene_program_geometry_fields']
        self.assertAlmostEqual(fields['xy'][0], -.01)
        self.assertAlmostEqual(fields['xy'][1], 2)
        self.assertAlmostEqual(item['base_z_if_supported_by_ground_m'], 1.81)
        self.assertEqual(fields['size'], [.4, .2, .4])
        self.assertEqual(fields['mesh_transform']['orientation_deg_xyz'], [0, 0, 0])
        self.assertNotIn('base_z', fields)
        self.assertNotIn('support', fields)

    def test_relative_inverse_depth_uses_two_known_sizes_for_metric_estimate(self):
        depth = np.zeros((80, 100), dtype=np.float32)
        depth[30:50, 10:30] = 1.1
        depth[30:50, 40:60] = 2.1
        depth[30:50, 70:90] = 1.4333333
        objects = [
            {'object_id': 'wide_anchor', 'mask': self.mask(10, 30, 30, 50)},
            {'object_id': 'narrow_anchor', 'mask': self.mask(40, 30, 60, 50)},
            {'object_id': 'target', 'mask': self.mask(70, 30, 90, 50)},
        ]
        result = analyze_reference_layout(
            depth, objects, self.image_size, depth_type='relative_inverse_depth', units='arbitrary',
            intrinsics_px=self.intrinsics, camera_to_world=self.camera_to_world,
            scale_anchors=[{'object_id': 'wide_anchor', 'axis': 'x', 'size_m': .4},
                           {'object_id': 'narrow_anchor', 'axis': 'x', 'size_m': .2}])

        self.assertEqual(result['metric_scale']['source'], 'known_size_anchors')
        target = result['objects'][2]
        self.assertAlmostEqual(target['depth']['distance_m'], 1.5, places=4)
        self.assertAlmostEqual(target['projected_size_m']['width_m'], .3, places=4)

    def test_uncalibrated_relative_depth_stays_relative(self):
        depth = np.zeros((80, 100), dtype=np.float32)
        depth[30:50, 10:30] = .8
        depth[30:50, 40:60] = .2
        result = analyze_reference_layout(
            depth,
            [{'object_id': 'near', 'mask': self.mask(10, 30, 30, 50)},
             {'object_id': 'far', 'mask': self.mask(40, 30, 60, 50)}],
            self.image_size, depth_type='relative_inverse_depth', units='arbitrary',
            intrinsics_px=self.intrinsics)

        self.assertFalse(result['metric_scale']['available'])
        self.assertEqual(result['objects'][0]['depth']['relative_forward_rank'], 0)
        self.assertEqual(result['objects'][1]['depth']['relative_forward_rank'], 1)
        self.assertNotIn('camera_position_m', result['objects'][0])
        self.assertNotIn('projected_size_m', result['objects'][0])

    def test_scene_camera_calibration_derives_pixel_intrinsics_and_world_pose(self):
        camera = {'position': [0, 0, 2], 'target': [0, 1, 2], 'up': [0, 0, 1],
                  'focal_length_mm': 50, 'horizontal_aperture_mm': 36}
        calibration = scene_camera_calibration(camera, self.image_size)
        self.assertAlmostEqual(calibration['intrinsics_px'][0][0], 100 * 50 / 36)
        self.assertEqual(calibration['camera_to_world'][0][3], 0)
        self.assertEqual(calibration['camera_to_world'][2][3], 2)

    def test_scene_camera_defaults_and_vertical_view_are_supported(self):
        calibration = scene_camera_calibration(
            {'position': [0, 0, 2], 'target': [0, 0, 1]}, self.image_size)
        self.assertAlmostEqual(calibration['intrinsics_px'][0][0], 100 * 24 / 36)
        self.assertAlmostEqual(np.linalg.det(np.asarray(calibration['camera_to_world'])[:3, :3]), 1)

    def test_registered_vertices_give_tighter_rotated_mesh_bounds_than_a_box(self):
        rotation = np.array([
            [np.sqrt(.5), -np.sqrt(.5), 0],
            [np.sqrt(.5), np.sqrt(.5), 0],
            [0, 0, 1],
        ])
        camera_to_world = np.eye(4)
        camera_to_world[:3, :3] = rotation
        vertices = np.array([
            [-1, -.1, -.5], [1, .1, .5], [-1, .1, -.5], [1, -.1, .5],
        ])
        bounds = [vertices.min(axis=0).tolist(), vertices.max(axis=0).tolist()]
        args = (bounds, {'width_m': 2, 'height_m': 1}, camera_to_world)
        exact = _mesh_fit(*args, vertices=vertices)
        conservative = _mesh_fit(*args)

        self.assertAlmostEqual(exact['world_aabb_size_m'][1], np.sqrt(.5))
        self.assertGreater(conservative['world_aabb_size_m'][1], exact['world_aabb_size_m'][1])

    def test_empty_or_mismatched_mask_is_rejected(self):
        depth = np.ones((80, 100), dtype=np.float32)
        with self.assertRaisesRegex(ValueError, 'mask is empty'):
            analyze_reference_layout(depth, [{'object_id': 'empty', 'mask': np.zeros((80, 100))}],
                                     self.image_size, depth_type='metric_depth', units='meter')
        with self.assertRaisesRegex(ValueError, 'dimensions must match'):
            analyze_reference_layout(depth, [{'object_id': 'bad', 'mask': np.zeros((40, 50))}],
                                     self.image_size, depth_type='metric_depth', units='meter')


if __name__ == '__main__':
    unittest.main()
