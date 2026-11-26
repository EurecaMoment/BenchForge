"""Regression coverage for registered glTF meshes compiled for A100 scene capture."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from PIL import Image
from pxr import Usd, UsdGeom, UsdPhysics, UsdShade
import trimesh

from capabilities.isaac_sim.asset_bundle import write_mesh_layer
from capabilities.isaac_sim.authoring import compile_scene
from capabilities.isaac_sim.mesh_materials import prepare_mesh_textures
from capabilities.isaac_sim.scene_requirements import UnsupportedScene
from tests.test_asset_capture_contract import scene_program


class MeshMaterialTests(unittest.TestCase):
    # 为每个测试建立独立的注册资产目录。
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.asset = self.root / 'asset_1111111111111111'
        self.asset.mkdir()

    # 两块分离网格应保留各自的顶点、可见材质和法线，同时共用完整碰撞体。
    def test_split_material_preserves_attributes_and_whole_collider(self):
        coords = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [2, 0, 1], [3, 0, 1], [2, 1, 1]]
        uvs = [[0, 0], [0.2, 0], [0, 0.2], [1, 1], [0.8, 1], [1, 0.8]]
        colors = [[1, 0, 0], [0, 1, 0], [0, 0, 1]] * 2
        source_faces = [[5, 3, 4], [2, 0, 1]]
        record = {'vertices': coords, 'faces': source_faces, 'coordinate_frame': 'z_up',
                  'texcoords': uvs, 'colors': colors, 'material_index': [1, 0],
                  'materials': [{}, {'textures': {'base_color': 'base.png'}}]}
        Image.new('RGB', (2, 2)).save(self.asset / 'base.png')
        program = scene_program()
        program['objects'][1].update(kind='mesh', asset_id=self.asset.name)
        for interpolation in ('vertex', 'faceVarying'):
            with self.subTest(normals=interpolation):
                record['normals_interpolation'] = interpolation
                up, right = [0, 0, 1], [1, 0, 0]
                record['normals'] = ([up] * 3 + [right] * 3 if interpolation == 'vertex'
                                     else [right] * 3 + [up] * 3)
                (self.asset / 'mesh.json').write_text(json.dumps(record))
                compiled = compile_scene(program, self.root, {})
                stage = Usd.Stage.CreateInMemory()
                self.assertTrue(stage.GetRootLayer().ImportFromString(compiled['usd']))
                packed = {}
                binary = compile_scene(program, self.root, {}, packed_meshes=packed)
                target = self.root / f'{interpolation}.usdc'
                write_mesh_layer(binary['usd'], packed, target)
                binary_stage = Usd.Stage.Open(str(target))
                for path, attributes in packed.items():
                    for name in attributes:
                        expected = stage.GetPrimAtPath(path).GetAttribute(name)
                        actual = binary_stage.GetPrimAtPath(path).GetAttribute(name)
                        self.assertEqual(expected.Get(), actual.Get())
                        self.assertEqual(expected.GetMetadata('interpolation'), actual.GetMetadata('interpolation'))
                parts = stage.GetPrimAtPath('/World/Objects/cube').GetChildren()
                collider, colored, textured = parts
                self.assertTrue(collider.HasAPI(UsdPhysics.CollisionAPI))
                self.assertEqual(UsdGeom.Imageable(collider).ComputeVisibility(), 'invisible')
                self.assertEqual(UsdGeom.Mesh(collider).GetFaceVertexIndicesAttr().Get(),
                                 [5, 3, 4, 2, 0, 1])
                self.assertEqual(len(compiled['entities']['cube']['geometry_parts']), 1)
                for visual, normal in [(colored, up), (textured, right)]:
                    self.assertFalse(visual.HasAPI(UsdPhysics.CollisionAPI))
                    part = UsdGeom.Mesh(visual)
                    self.assertEqual(len(part.GetPointsAttr().Get()), 3)
                    self.assertEqual(part.GetFaceVertexIndicesAttr().Get(), [2, 0, 1])
                    self.assertEqual(part.GetNormalsInterpolation(), interpolation)
                    np.testing.assert_allclose(part.GetNormalsAttr().Get(), [normal] * 3)
                np.testing.assert_allclose(UsdGeom.Mesh(colored).GetDisplayColorPrimvar().Get(),
                                           [colors[2], colors[0], colors[1]])
                np.testing.assert_allclose(UsdGeom.PrimvarsAPI(textured).GetPrimvar('st').Get(), uvs[3:])
                bound = UsdShade.MaterialBindingAPI(textured).ComputeBoundMaterial()[0]
                self.assertTrue(bound.ComputeSurfaceSource('mdl')[0])

    # OPAQUE 忽略纹理和因子中的透明度，MASK 与 BLEND 仅在可见性真的改变时拒绝。
    def test_alpha_modes(self):
        Image.new('RGBA', (2, 2), (18, 45, 90, 128)).save(self.asset / 'color.png')
        for mode, cutoff, supported in [('OPAQUE', None, True), ('MASK', .1, True),
                                        ('MASK', .4, False), ('BLEND', None, False)]:
            with self.subTest(mode=mode, cutoff=cutoff):
                material = {'textures': {'base_color': 'color.png'}, 'alpha_mode': mode,
                            'base_color_factor': [1, 1, 1, .5]}
                if cutoff is not None:
                    material['alpha_cutoff'] = cutoff
                mesh = {'vertices': [[0, 0, 0]], 'texcoords': [[.5, .5]], 'materials': [material]}
                if supported:
                    _, files = prepare_mesh_textures(mesh, self.asset, self.asset.name)
                    self.assertEqual(len(files), 1)
                else:
                    with self.assertRaisesRegex(UnsupportedScene, 'base-color opacity'):
                        prepare_mesh_textures(mesh, self.asset, self.asset.name)

    # 旧登记记录从原 GLB 读取不透明语义，全程保留登记文件与透明纹理的原始字节。
    def test_legacy_alpha_uses_retained_glb(self):
        image = Image.new('RGBA', (2, 2), (18, 45, 90, 0))
        image.save(self.asset / 'color.png')
        source = trimesh.creation.box()
        source.visual = trimesh.visual.texture.TextureVisuals(
            uv=np.zeros((len(source.vertices), 2)),
            material=trimesh.visual.material.PBRMaterial(baseColorTexture=image, alphaMode='OPAQUE'))
        source.export(self.asset / 'asset.glb')
        row = {'textures': {'base_color': 'color.png'}, 'source_material_type': 'PBRMaterial'}
        mesh = {'vertices': [[0, 0, 0]], 'texcoords': [[.5, .5]], 'materials': [row]}
        prepared, _ = prepare_mesh_textures(mesh, self.asset, self.asset.name)
        self.assertEqual(prepared['materials'][0]['alpha_mode'], 'OPAQUE')
        self.assertNotIn('alpha_mode', row)
        self.assertEqual(Image.open(self.asset / 'color.png').getpixel((0, 0))[3], 0)
