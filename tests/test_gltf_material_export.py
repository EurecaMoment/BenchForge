"""glTF material semantics kept in registered mesh data for A100 capture."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from PIL import Image
import trimesh

from capabilities.asset_generation.mesh_export import export_mesh


class GltfMaterialExportTests(unittest.TestCase):
    # 将真实的二进制 glTF 经常规登记入口导入，读取传给场景编译器的材质。
    def register(self, **fields):
        geometry = trimesh.creation.box()
        geometry.visual = trimesh.visual.texture.TextureVisuals(
            uv=geometry.vertices[:, :2] + .5,
            material=trimesh.visual.material.PBRMaterial(
                baseColorTexture=Image.new('RGBA', (2, 2), (100, 90, 20, 128)),
                **fields,
            ),
        )
        with TemporaryDirectory() as folder:
            root = Path(folder)
            asset = root / 'material.glb'
            geometry.export(asset)
            mesh = export_mesh(asset, root / 'mesh.json', source_frame='y_up')
            return mesh['materials'][0]

    # 缺省乘数应为 1，带 alpha 字节的底色纹理仍按 glTF 缺省的 OPAQUE 登记。
    def test_unspecified_material_properties(self):
        material = self.register()
        self.assertEqual((material['metallic_factor'], material['roughness_factor']), (1., 1.))
        self.assertEqual(material['alpha_mode'], 'OPAQUE')
        self.assertNotIn('alpha_cutoff', material)
        self.assertIn('base_color', material['textures'])

    # 保留显式零乘数和零阈值，未提供的 MASK 阈值采用 glTF 缺省值。
    def test_mask_cutoff_and_zero_factors(self):
        for options, expected in [({}, .5), ({'alphaCutoff': 0.}, 0.), ({'alphaCutoff': .7}, .7)]:
            with self.subTest(options=options):
                material = self.register(alphaMode='MASK', metallicFactor=0., roughnessFactor=0.,
                                         **options)
                self.assertEqual((material['metallic_factor'], material['roughness_factor']), (0., 0.))
                self.assertEqual(material['alpha_mode'], 'MASK')
                self.assertAlmostEqual(material['alpha_cutoff'], expected)

    # 透明混合不使用裁切阈值，必须独立保留以免误判为不透明。
    def test_blend_mode(self):
        material = self.register(alphaMode='BLEND')
        self.assertEqual(material['alpha_mode'], 'BLEND')
        self.assertNotIn('alpha_cutoff', material)
