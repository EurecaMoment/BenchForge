"""普通可见资产默认用 FastTrellis，原生表示仅限明确例外。"""
import unittest

from capabilities.scene_authoring.validation.asset_policy import is_structural_geometry
from capabilities.scene_authoring.validation.asset_policy import layout_native_reason
from capabilities.scene_authoring.validation.asset_policy import layout_representation


class AssetRepresentationPolicyTests(unittest.TestCase):
    def test_static_props_and_robot_shells_default_to_trellis(self):
        for item in (
            {'label': 'chair', 'requires_articulation': False},
            {'label': 'static drone shell', 'requires_articulation': False},
            {'label': 'Unitree Go2 visual shell', 'requires_articulation': False},
            {'label': 'floor-standing water cooler', 'requires_articulation': False},
            {'label': 'road sign', 'requires_articulation': False},
        ):
            with self.subTest(label=item['label']):
                self.assertEqual(layout_representation(item)[0], 'trellis')

    def test_native_is_limited_to_fixed_structure_or_explicit_articulation(self):
        for label in ('floor', 'concrete wall panel', 'door frame', '楼梯'):
            with self.subTest(label=label):
                self.assertEqual(layout_representation({'label': label})[0], 'native')

        self.assertEqual(layout_representation({
            'label': 'Unitree Go2', 'requires_articulation': True,
        })[0], 'native')

    def test_ordinary_object_cannot_bypass_trellis_with_native_hint(self):
        representation, reason = layout_representation({
            'label': 'office chair', 'representation': 'native',
        })

        self.assertEqual(representation, 'trellis')
        self.assertIsNotNone(reason)

    def test_prop_names_that_mention_a_surface_are_not_structural(self):
        for label in ('chair against wall', 'ceiling fan', 'wall art', 'floor lamp',
                      'floor-standing water cooler', 'road sign'):
            with self.subTest(label=label):
                reason = layout_native_reason(label)
                self.assertIsNone(reason)
                self.assertFalse(is_structural_geometry({
                    'id': 'prop', 'label': label, 'size': [0.5, 0.5, 1.2], 'dynamic': False,
                }))

    def test_structural_geometry_requires_architecture_label_or_tag(self):
        self.assertTrue(is_structural_geometry({
            'id': 'floor_slab_main', 'label': 'slab', 'size': [4, 5, 0.2], 'dynamic': False,
        }))
        self.assertTrue(is_structural_geometry({
            'id': 'part_1', 'label': 'part', 'tags': ['architectural_structure'],
            'size': [4, 5, 0.2], 'dynamic': False,
        }))


if __name__ == '__main__':
    unittest.main()
