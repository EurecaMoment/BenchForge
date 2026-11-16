import json
import os
from unittest.mock import patch
from pipelines.scene_production.service import create_store
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import trimesh
from pxr import Usd, UsdGeom, UsdPhysics, UsdShade

from capabilities.asset_generation.mesh_export import export_mesh
from capabilities.isaac_sim.authoring import compile_scene, UnsupportedScene
from capabilities.isaac_sim.native_geometry import exported_entities
from test_isaac_sim_evidence import scene_program


class AssetCaptureContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.identity = "asset_0123456789abcdef"
        self.asset = self.root / self.identity
        self.asset.mkdir()

    def compile(self, record):
        (self.asset / "mesh.json").write_text(json.dumps(record), encoding="utf-8")
        program = scene_program()
        program["objects"][1].update(kind="mesh", asset_id=self.identity)
        return compile_scene(program, self.root, {})

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

    def test_texture_data_cannot_be_silently_dropped(self):
        record = {
            "vertices": [[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]],
            "faces": [[0, 1, 2]],
            "coordinate_frame": "z_up",
            "materials": [{"textures": {"base_color": "texture.png"}}],
        }
        with self.assertRaises(UnsupportedScene):
            self.compile(record)

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

    def test_unsupported_scene_actions_are_rejected_before_queueing(self):
        store = create_store(self.root, database_url="sqlite:///" + str(self.root / "queue.sqlite"))
        self.addCleanup(store.repo.engine.dispose)
        with patch.dict(os.environ, {"BENCHFORGE_EXECUTION_MODE": "exploration"}):
            with self.assertRaisesRegex(ValueError, "native session"):
                store.submit(
                    "unsupported",
                    [{"execution_mode": "exploration"}],
                    submitted_programs=[{"program": {"interactions": [{"action": "robot_push"}]}}],
                )
        self.assertEqual(store.all_work(), [])
