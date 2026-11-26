import json
import hashlib
import os
from unittest.mock import patch
from pipelines.scene_production.service import create_store
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from tests.database_fixtures import use_postgres

import numpy as np
from PIL import Image
import trimesh
from pxr import Usd, UsdGeom, UsdPhysics, UsdShade

from capabilities.asset_generation.mesh_export import export_mesh
from capabilities.isaac_sim.asset_bundle import (
    export_binary_usd, stage_texture_dependencies, upload_asset_bundle,
)
from capabilities.isaac_sim.authoring import compile_scene, UnsupportedScene
from capabilities.isaac_sim.native_geometry import exported_entities


# 构造固定双视角场景，避免通过其他测试模块间接加载 gRPC 运行时。
def scene_program():
    return {'schema': 'benchforge.scene/v1', 'scene_id': 'pbr_contract',
        'title': 'PBR contract', 'assumptions': ['Synthetic test geometry'],
        'objects': [
            {'id': 'floor', 'label': 'floor', 'kind': 'box', 'size': [4, 4, .1],
             'xy': [0, 0], 'base_z': -.1, 'support': 'ground', 'dynamic': False, 'mass_kg': 0,
             'color': [.4, .4, .4]},
            {'id': 'cube', 'label': 'PBR mesh', 'kind': 'box', 'size': [.2, .2, .2],
             'xy': [0, 0], 'base_z': 1, 'support': 'ground', 'dynamic': True, 'mass_kg': 1,
             'color': [.8, .1, .1]}],
        'cameras': [{'id': 'a', 'position': [2, -2, 2], 'target': [0, 0, .1]},
                    {'id': 'b', 'position': [-2, -2, 2], 'target': [0, 0, .1]}]}


class AssetCaptureContractTests(unittest.TestCase):
    # 建立临时资源目录和固定网格身份，供导出与采集契约验证。
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.identity = "asset_0123456789abcdef"
        self.asset = self.root / self.identity
        self.asset.mkdir()

    # 保存指定网格元数据并替换示例对象，编译成实际采集场景。
    def compile(self, record):
        (self.asset / "mesh.json").write_text(json.dumps(record), encoding="utf-8")
        program = scene_program()
        program["objects"][1].update(kind="mesh", asset_id=self.identity)
        return compile_scene(program, self.root, {})

    # 验证真实无纹理 PLY 导出后可编译，且材质参数不丢失。
    def test_real_untextured_ply_export_compiles_and_preserves_material(self):
        source = self.root / "mesh.ply"
        trimesh.creation.icosphere(subdivisions=1).export(source)
        export_mesh(source, self.asset / "mesh.json")
        record = json.loads((self.asset / "mesh.json").read_text())
        self.assertTrue(record["materials"])
        self.assertFalse(record["materials"][0]["textures"])
        compiled = self.compile(record)
        stage = Usd.Stage.CreateInMemory()
        self.assertTrue(stage.GetRootLayer().ImportFromString(compiled["usd"]))
        shader = UsdShade.Shader(stage.GetPrimAtPath("/World/Materials/cube_Part0/Surface"))
        self.assertAlmostEqual(
            shader.GetInput("roughness").Get(), record["materials"][0]["roughness_factor"]
        )
        mesh = UsdGeom.Mesh(stage.GetPrimAtPath("/World/Objects/cube/Part0"))
        colors = UsdGeom.PrimvarsAPI(mesh).GetPrimvar("displayColor")
        self.assertEqual(colors.GetInterpolation(), "faceVarying")
        self.assertEqual(len(colors.Get()), len(record["faces"]) * 3)

    def test_textured_glb_compiles_to_a100_omnipbr_and_preserves_registry(self):
        mesh = trimesh.creation.icosphere(subdivisions=1)
        mesh.visual = trimesh.visual.texture.TextureVisuals(
            uv=mesh.vertices[:, :2] * 0.5 + 0.5,
            material=trimesh.visual.material.PBRMaterial(
                baseColorTexture=Image.new("RGB", (2, 2), (120, 90, 30)),
                metallicRoughnessTexture=Image.new("RGB", (2, 2), (255, 64, 128)),
                roughnessFactor=0.5,
                metallicFactor=0.5,
            ),
        )
        source = self.root / "textured.glb"
        mesh.export(source)
        record = export_mesh(source, self.asset / "mesh.json", self.asset / "source.glb")
        textures = record["materials"][0]["textures"]
        self.assertEqual(set(textures), {"base_color", "metallic_roughness"})
        paths = ["mesh.json", "source.glb", *textures.values()]
        registered = {path: (self.asset / path).read_bytes() for path in paths}
        program = scene_program()
        program["objects"][1].update(kind="mesh", asset_id=self.identity)
        compiled = compile_scene(program, self.root, {})
        self.assertEqual(len(compiled["texture_dependencies"]), 2)
        self.assertIn('string info:mdl:sourceAsset:subIdentifier = "OmniPBR"', compiled["usd"])
        self.assertIn('texCoord2f[] primvars:st', compiled["usd"])
        self.assertIn('bool inputs:enable_ORM_texture = true', compiled["usd"])
        self.assertIn('float inputs:RoughnessMultiplier = 0.5', compiled["usd"])
        self.assertIn('float inputs:metallic_constant = 1.0', compiled["usd"])

        stage = Usd.Stage.CreateInMemory()
        self.assertTrue(stage.GetRootLayer().ImportFromString(compiled["usd"]))
        mesh_prim = stage.GetPrimAtPath("/World/Objects/cube/Part0")
        primvar = UsdGeom.PrimvarsAPI(mesh_prim).GetPrimvar("st")
        self.assertEqual(primvar.GetInterpolation(), "vertex")
        shader = UsdShade.Shader(stage.GetPrimAtPath("/World/Materials/cube_Part0/Surface"))
        self.assertEqual(shader.GetPrim().GetAttribute(
            "info:mdl:sourceAsset:subIdentifier").Get(), "OmniPBR")

        package = self.root / "attempt"
        package.mkdir()
        scene = package / "scene.usda"
        scene.write_text(compiled["usd"], encoding="utf-8")
        dependencies = stage_texture_dependencies(compiled["texture_dependencies"], package)
        binary_scene = export_binary_usd(scene, package / "scene.usdc")
        self.assertLess(binary_scene.stat().st_size, scene.stat().st_size)
        binary_stage = Usd.Stage.Open(str(binary_scene))
        binary_mesh = UsdGeom.Mesh(binary_stage.GetPrimAtPath("/World/Objects/cube/Part0"))
        self.assertEqual(len(binary_mesh.GetPointsAttr().Get()), len(record["vertices"]))
        self.assertEqual(len(binary_mesh.GetFaceVertexIndicesAttr().Get()), len(record["faces"]) * 3)
        binary_shader = UsdShade.Shader(
            binary_stage.GetPrimAtPath("/World/Materials/cube_Part0/Surface")
        )
        self.assertEqual(binary_shader.GetPrim().GetAttribute(
            "info:mdl:sourceAsset:subIdentifier").Get(), "OmniPBR")
        texture_paths = []
        for slot in ("diffuse_texture", "ORM_texture"):
            reference = binary_shader.GetInput(slot).Get()
            relative = Path(reference.path)
            self.assertFalse(relative.is_absolute(), reference.path)
            self.assertTrue((package / relative).is_file(), reference.path)
            texture_paths.append(relative.as_posix())
        self.assertCountEqual(texture_paths, [item["relative_path"] for item in dependencies])

        class RecordingClient:
            def __init__(self):
                self.uploads = []

            def upload(self, path, *, asset_id, relative_path):
                with path.open("rb") as stream:
                    digest = hashlib.file_digest(stream, "sha256").hexdigest()
                result = {"asset_id": asset_id, "relative_path": relative_path,
                          "size_bytes": str(path.stat().st_size), "sha256": digest}
                self.uploads.append(result)
                return result

        client = RecordingClient()
        asset = upload_asset_bundle(client, binary_scene, package, dependencies)
        self.assertEqual(asset["relative_path"], "scene.usdc")
        self.assertEqual(len(client.uploads), 3)
        self.assertEqual(len({upload["asset_id"] for upload in client.uploads}), 1)
        self.assertEqual({upload["relative_path"] for upload in client.uploads},
                         {"scene.usdc", *(item["relative_path"] for item in dependencies)})
        metallic = next(item["path"] for item in dependencies
                        if "metallic_roughness" in item["relative_path"])
        with Image.open(metallic) as image:
            self.assertEqual(image.getpixel((0, 0))[2], 64)
        self.assertEqual(registered, {path: (self.asset / path).read_bytes() for path in paths})

    def test_unmapped_emissive_factor_fails_before_capture(self):
        record = {
            "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "faces": [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]],
            "coordinate_frame": "z_up",
            "materials": [{"emissive_factor": [0.2, 0, 0], "textures": {}}],
        }
        with self.assertRaisesRegex(UnsupportedScene, "emissive_factor"):
            self.compile(record)

    def test_nonopaque_base_color_factor_fails_before_capture(self):
        record = {
            "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "faces": [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]],
            "coordinate_frame": "z_up",
            "materials": [{"base_color_factor": [1, 1, 1, 0.5], "textures": {}}],
        }
        with self.assertRaisesRegex(UnsupportedScene, "base-color opacity"):
            self.compile(record)

    # 验证无纹理 GLB 的颜色因子在导出和编译链路只应用一次。
    def test_untextured_glb_factor_is_applied_once(self):
        mesh = trimesh.creation.icosphere(subdivisions=1)
        mesh.visual = trimesh.visual.texture.TextureVisuals(
            uv=np.zeros((len(mesh.vertices), 2)),
            material=trimesh.visual.material.PBRMaterial(
                baseColorFactor=[128, 64, 32, 255], roughnessFactor=0.3
            ),
        )
        source = self.root / "colored.glb"
        mesh.export(source)
        record = export_mesh(source, self.asset / "mesh.json")
        for legacy in (False, True):
            if legacy:
                record["materials"][0].pop("vertex_colors_include_base_factor")
            stage = Usd.Stage.CreateInMemory()
            stage.GetRootLayer().ImportFromString(self.compile(record)["usd"])
            geometry = UsdGeom.Mesh(stage.GetPrimAtPath("/World/Objects/cube/Part0"))
            colors = UsdGeom.PrimvarsAPI(geometry).GetPrimvar("displayColor").Get()
            self.assertTrue(np.allclose(colors[0], np.array([128, 64, 32]) / 255, atol=1e-5))

    # 验证逐面角点法线在原生场景中保持 face-varying 语义。
    def test_corner_normals_remain_face_varying(self):
        record = {
            "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "faces": [[0, 1, 2], [0, 1, 3]],
            "coordinate_frame": "z_up",
            "normals": [[0, 0, 1]] * 3 + [[0, -1, 0]] * 3,
            "normals_interpolation": "faceVarying",
        }
        compiled = self.compile(record)
        stage = Usd.Stage.CreateInMemory()
        stage.GetRootLayer().ImportFromString(compiled["usd"])
        mesh = UsdGeom.Mesh(stage.GetPrimAtPath("/World/Objects/cube/Part0"))
        self.assertEqual(mesh.GetNormalsInterpolation(), "faceVarying")
        self.assertEqual(len(mesh.GetNormalsAttr().Get()), 6)

    def test_missing_corner_normals_fall_back_without_degenerate_triangles(self):
        record = {
            "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "faces": [[0, 1, 2], [0, 1, 1], [0, 1, 3]],
            "coordinate_frame": "z_up",
            "normals": [[0, 0, 1]] * 3 + [[0, 0, 0]] * 3 + [[0, -1, 0], [0, 0, 0], [0, -1, 0]],
            "normals_interpolation": "faceVarying",
        }
        stage = Usd.Stage.CreateInMemory()
        stage.GetRootLayer().ImportFromString(self.compile(record)["usd"])
        mesh = UsdGeom.Mesh(stage.GetPrimAtPath("/World/Objects/cube/Part0"))
        self.assertEqual(list(mesh.GetFaceVertexIndicesAttr().Get()), [0, 1, 2, 0, 1, 3])
        self.assertEqual(mesh.GetNormalsInterpolation(), "faceVarying")
        np.testing.assert_allclose(mesh.GetNormalsAttr().Get(), [[0, 0, 1]] * 3 + [[0, -1, 0]] * 3)

    # 验证原生导出依据真实网格点计算几何，并要求可用碰撞体。
    def test_native_export_measures_actual_points_and_requires_colliders(self):
        file = self.root / "native.usdc"
        stage = Usd.Stage.CreateNew(str(file))
        root = UsdGeom.Xform.Define(stage, "/World/Scene/Objects/sofa")
        root.AddTranslateOp().Set((3, 2, 1))
        mesh = UsdGeom.Mesh.Define(stage, str(root.GetPath()) + "/mesh")
        mesh.CreatePointsAttr([(-1, -2, 0), (1, 2, 0), (0, 0, 2)])
        mesh.CreateFaceVertexCountsAttr([3])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2])
        UsdPhysics.CollisionAPI.Apply(mesh.GetPrim()).CreateCollisionEnabledAttr(True)
        stage.GetRootLayer().Save()
        declared = {
            "sofa": {"id": "sofa", "prim_path": str(root.GetPath()), "size": [100, 100, 100]}
        }
        measured = exported_entities(file, declared)["sofa"]
        self.assertTrue(np.allclose(measured["world_aabb"]["min"], [2, 0, 1]))
        self.assertTrue(np.allclose(measured["world_aabb"]["max"], [4, 4, 3]))
        self.assertEqual(len(measured["geometry_evidence"]["sha256"]), 64)
        UsdPhysics.CollisionAPI(mesh).GetCollisionEnabledAttr().Set(False)
        stage.GetRootLayer().Save()
        with self.assertRaisesRegex(ValueError, "colliders"):
            exported_entities(file, declared)

    # 验证当前适配器不支持的场景动作在入队前就被拒绝。
    def test_unsupported_scene_actions_are_rejected_before_queueing(self):
        store = create_store(self.root, database_url=use_postgres(self))
        self.addCleanup(store.repo.engine.dispose)
        with patch.dict(os.environ, {"BENCHFORGE_EXECUTION_MODE": "exploration"}):
            with self.assertRaisesRegex(ValueError, "native session"):
                store.submit(
                    "unsupported",
                    [{"execution_mode": "exploration"}],
                    submitted_programs=[{"program": {"interactions": [{"action": "robot_push"}]}}],
                )
        self.assertEqual(store.all_work(), [])
