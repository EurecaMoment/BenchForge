import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from PIL import Image

from capabilities.isaac_sim.authoring import compile_scene, UnsupportedScene
from capabilities.isaac_sim.capture_stage import _native_imports
from capabilities.isaac_sim.evidence import measured_entities, save_frame
from storage.files import write_json


# 构造含双相机、材质与刚体的示例场景，供原生编译和证据适配验证。
def scene_program(name='contract'):
    return {'schema': 'benchforge.scene/v1', 'scene_id': name, 'title': 'Two cameras',
        'assumptions': ['Synthetic test geometry'],
        'objects': [
            {'id': 'floor', 'label': 'floor', 'kind': 'box', 'size': [4, 4, .1],
             'xy': [0, 0], 'base_z': -.1, 'support': 'ground', 'dynamic': False, 'mass_kg': 0,
             'color': [.4, .4, .4]},
            {'id': 'cube', 'label': 'red cube', 'kind': 'box', 'size': [.2, .2, .2],
             'xy': [0, 0], 'base_z': 1, 'support': 'ground', 'dynamic': True, 'mass_kg': 1,
             'color': [.8, .1, .1]}],
        'cameras': [{'id': 'a', 'position': [2, -2, 2], 'target': [0, 0, .1]},
                    {'id': 'b', 'position': [-2, -2, 2], 'target': [0, 0, .1]}]}


class EvidenceContractTests(unittest.TestCase):
    # 验证原生资源导入保留质量、摩擦等物理参数及源坐标轴语义。
    def test_native_import_keeps_physical_parameters_and_source_axis(self):
        class Requests:
            # 初始化模拟原生请求的调用记录。
            def __init__(self):
                self.calls = []

            # 记录扩展操作及参数，并返回最小关节根回执以驱动导入测试。
            def extension(self, key, operation, arguments):
                self.calls.append((operation, arguments))
                return None, {'articulation_roots': []}

        requests = Requests()
        contact = {'static_friction': .6, 'dynamic_friction': .4, 'restitution': 0}
        compiled = {'imports': [{
            'object': {'id': 'cup', 'label': 'cup', 'dynamic': True,
                       'mass_kg': .35, 'physics': contact, 'size': [.2, .2, .3]},
            'source': {'asset_id': 'native', 'relative_path': 'cup.usd'},
            'catalog': {'source_up_axis': 'Y', 'size': [2, 2, 3]},
            'path': '/World/Scene/cup', 'center': [0, 0, .15],
        }], 'entities': {'cup': {}}}
        _native_imports(requests, 'scene-session', compiled)
        operation, imported = requests.calls[0]
        self.assertEqual(operation, 'asset.import')
        self.assertEqual(imported['source'], {'asset_id': 'native', 'relative_path': 'cup.usd'})
        self.assertFalse(imported['fix_base'])
        self.assertEqual(imported['mass_kg'], .35)
        self.assertEqual(imported['physics'], contact)
        self.assertEqual(imported['source_up_axis'], 'Y')
        self.assertEqual(requests.calls[1][0], 'scene.edit')

    # 验证可移植场景编译保留相机、材质和真实刚体物理声明。
    def test_portable_scene_preserves_cameras_materials_and_real_physics(self):
        compiled = compile_scene(scene_program(), Path('.'), {})
        self.assertEqual(len(compiled['cameras']), 2)
        self.assertIn('PhysicsRigidBodyAPI', compiled['usd'])
        self.assertIn('PhysicsMaterialAPI', compiled['usd'])
        self.assertIn('a100sim:semanticLabel', compiled['usd'])
        self.assertIn('custom string benchforge:intensityUnit = "linear_radiance"', compiled['usd'])
        self.assertNotIn('isaacsim', compiled['usd'])
        self.assertEqual(compiled['entities']['cube']['initial_center'], [0, 0, 1.1])
        program = scene_program()
        program['render_environment'] = {'renderer': 'RaytracedLighting'}
        self.assertEqual(compile_scene(program, Path('.'), {})['usd'], compiled['usd'])

    # 验证证据读取采用仿真步进后的变换，而非编译时初始位姿。
    def test_post_step_native_transform_is_used_instead_of_initial_pose(self):
        compiled = compile_scene(scene_program(), Path('.'), {})
        compiled['entities'] = {'cube': compiled['entities']['cube']}
        root = '/World/Scene/Objects/cube'
        world = np.eye(4)
        world[3, :3] = [0, 0, .1]
        part = world.copy()
        part[:3, :3] *= .2
        prims = [{'path': root, 'world_matrix': world.tolist()},
                 {'path': root + '/Part0', 'world_matrix': part.tolist(),
                  'apis': ['PhysicsCollisionAPI'],
                  'attributes': {'physics:collisionEnabled': 'True', 'size': '1'}}]
        measured = measured_entities(compiled, prims)['cube']
        self.assertEqual(measured['initial_center'], [0, 0, 1.1])
        np.testing.assert_allclose(measured['world_aabb']['min'], [-.1, -.1, 0], atol=1e-7)
        self.assertIn('world meters', measured['world_transform_convention'])

    # 验证原生标注适配继续满足工作台展示与相机标定契约。
    def test_native_annotations_keep_the_existing_workbench_and_calibration_contract(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            image, arrays, labels = root / 'render.png', root / 'annotations.npz', root / 'labels.json'
            pixels = np.arange(64 * 64 * 3, dtype=np.uint8).reshape(64, 64, 3)
            Image.fromarray(pixels).save(image)
            instances = np.zeros((64, 64), dtype=np.uint32)
            instances[10:20, 20:30] = 1
            np.savez(arrays, depth_m=np.ones((64, 64)), instance_id=instances,
                semantic_id=instances, normal_world=np.zeros((64, 64, 3)),
                range_m=np.ones((64, 64)), valid=np.ones((64, 64), dtype=bool))
            write_json(labels, {'depth_convention': 'camera-forward Z', 'backend': 'contract-fixture',
                'classes': {'1': 'red cube'},
                'instances': {'1': {'prim_path': '/World/Scene/Objects/cube/Part0'}}})
            files = [{'artifact_id': key, 'source_path': str(file)} for key, file in
                     [('rgb', image), ('npz', arrays), ('labels', labels)]]
            matrix = np.eye(4)
            matrix[:3, 3] = [1, 2, 3]
            frame = {'image': 'artifact://rgb', 'annotations': {
                'arrays': 'artifact://npz', 'labels': 'artifact://labels'},
                'camera': '/World/Scene/Cameras/View0', 'camera_world': matrix.tolist(),
                'resolution': [64, 64], 'fov_degrees': 90}
            output = root / 'capture'
            output.mkdir()
            result = save_frame(frame, files, 0, scene_program(), output, physics_tick=180, dt=1 / 60)
            self.assertEqual(result['image_size'], {'width': 64, 'height': 64})
            self.assertEqual(result['objects'][0]['bbox_2d']['xyxy'], [20, 10, 30, 20])
            self.assertEqual(result['objects'][0]['depth_z_median'], 1)
            self.assertEqual(result['camera']['T_world_camera_usd'][3][:3], [1, 2, 3])
            self.assertAlmostEqual(result['camera']['K'][0][0], 32)
            self.assertEqual(result['timestamp_sim'], 3)
            self.assertTrue(result['quality']['passed'])
            self.assertEqual(json.loads((output / 'view_0.json').read_text())['frame_id'], 'view_0')
            self.assertTrue((output / 'view_0_depth.npy').exists())


if __name__ == '__main__':
    unittest.main()
