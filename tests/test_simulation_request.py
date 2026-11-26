"""SceneProgram -> isaacsim_rpc.v1 SimulateRequest mapping contract."""
import math
import unittest

from capabilities.capture.capture_scope import capture_scope
from capabilities.isaac_sim.scene_mapping import (
    camera_view, simulation_plan, simulation_request,
)
from capabilities.isaac_sim.scene_requirements import UnsupportedScene
from capabilities.scene_authoring.catalog.native import ASSETS


# 构造最小合法对象，字段可覆盖以复现单个适配器限制。
def box(identity='cube', *, kind='box', size=(1, 1, 1), xy=(0, 0), base_z=0,
        support='ground', dynamic=False, **extra):
    return {'id': identity, 'label': identity, 'kind': kind, 'size': list(size),
            'xy': list(xy), 'base_z': base_z, 'support': support, 'dynamic': dynamic, **extra}


# 构造单相机程序，供视图与采样测试复用。
def program(objects, cameras=None):
    return {'schema': 'benchforge.scene/v1', 'scene_id': 'mapping', 'title': 'mapping',
            'objects': objects,
            'cameras': cameras or [{'position': [2, -2, 2], 'target': [0, 0, 1]}],
            'assumptions': []}


# 构造与部署配置同形的 provider 设置。
def settings(*, steps=120, target_frames=24, image_size=128, spp=16, mesh_assets=None):
    return {'simulation': {'steps': steps, 'dt': 1 / 60, 'gravity': 9.81, 'ground': True,
                           'capture_target_frames': target_frames, 'capture_every_steps': None},
            'render': {'image_size': image_size, 'samples_per_pixel': spp,
                       'fov_degrees': 50, 'seed': 0},
            'mesh_assets': dict(mesh_assets or {})}


class SimulationMappingTests(unittest.TestCase):
    # 断言映射在指定字段路径上明确拒绝，并返回全部问题路径。
    def rejected(self, value, configuration=None, scope=None):
        scope = scope or capture_scope(value)
        with self.assertRaises(UnsupportedScene) as caught:
            simulation_plan(value, configuration or settings(), scope)
        return {row['path'] for row in caught.exception.issues}

    # 盒体映射为协议 cube，世界中心由支撑与 base_z 计算。
    def test_box_maps_to_cube_at_the_computed_world_center(self):
        plan = simulation_plan(program([box(size=(2, 4, 6), base_z=1)]), settings(),
                               capture_scope(program([box()])))
        body = plan['bodies'][0]
        self.assertEqual(body['shape'], 'cube')
        self.assertEqual(body['position'], {'x': 0.0, 'y': 0.0, 'z': 4.0})
        self.assertEqual(body['size'], {'x': 2.0, 'y': 4.0, 'z': 6.0})
        self.assertNotIn('mass', body)

    # 动态对象保留质量；静态对象完全省略质量字段。
    def test_only_dynamic_bodies_declare_mass(self):
        value = program([box('still'), box('moving', dynamic=True, mass_kg=2.5)])
        plan = simulation_plan(value, settings(), capture_scope(value))
        self.assertNotIn('mass', plan['bodies'][0])
        self.assertEqual(plan['bodies'][1]['mass'], 2.5)
        self.assertFalse(plan['entities']['still']['dynamic'])
        self.assertTrue(plan['entities']['moving']['dynamic'])

    # 球体要求均匀尺寸以忠实映射半径，非均匀尺寸明确拒绝。
    def test_sphere_requires_uniform_size(self):
        scope = capture_scope(program([box('ball', kind='sphere', size=(1, 1, 1))]))
        plan = simulation_plan(program([box('ball', kind='sphere', size=(1, 1, 1))]),
                               settings(), scope)
        self.assertEqual(plan['bodies'][0]['shape'], 'sphere')
        self.assertEqual(plan['bodies'][0]['radius'], 0.5)
        value = program([box('oval', kind='sphere', size=(1, 2, 1))])
        self.assertIn('/objects/0/size', self.rejected(value))

    # 容器拆分为五块独立盒壁，保持开口顶部。
    def test_container_splits_into_five_box_walls(self):
        value = program([box('bin', kind='container', size=(1, 1, 1), base_z=0)])
        plan = simulation_plan(value, settings(), capture_scope(value))
        self.assertEqual(len(plan['bodies']), 5)
        self.assertEqual({body['shape'] for body in plan['bodies']}, {'cube'})
        self.assertEqual(plan['entities']['bin']['body_ids'],
                         [f'bin__part{index}' for index in range(5)])

    # 复合对象的 box/sphere 部件可表达，cylinder 部件明确拒绝。
    def test_composite_parts_support_only_box_and_sphere(self):
        parts = [{'shape': 'box', 'size': [1, 1, 1], 'offset': [0, 0, 0]},
                 {'shape': 'sphere', 'size': [.5, .5, .5], 'offset': [0, 0, .8]}]
        value = program([box('rig', kind='composite', size=(1, 1, 2), parts=parts)])
        plan = simulation_plan(value, settings(), capture_scope(value))
        self.assertEqual([body['shape'] for body in plan['bodies']], ['cube', 'sphere'])
        value['objects'][0]['parts'] = [{'shape': 'cylinder', 'size': [1, 1, 1], 'offset': [0, 0, 0]}]
        self.assertIn('/objects/0/parts/0/shape', self.rejected(value))

    # 协议无 BodySpec 的圆柱、以及上传/生成路线在提交前拒绝。
    def test_cylinder_native_and_generated_are_rejected(self):
        for kind in ('cylinder', 'generated'):
            with self.subTest(kind=kind):
                value = program([box('item', kind=kind)])
                self.assertIn('/objects/0/kind', self.rejected(value))
        native = program([box('item', kind='native',
                              native_asset={'asset_id': 'a', 'relative_path': 'a.usd',
                                            'size': [1, 1, 1], 'source_up_axis': 'Z'})])
        self.assertIn('/objects/0/kind', self.rejected(native))

    # 内建原生目录资产在 v2 body 契约中同样不支持。
    def test_builtin_native_asset_is_rejected(self):
        name = next(iter(ASSETS))
        value = program([box('item', kind=name)])
        self.assertIn('/objects/0/kind', self.rejected(value))

    # 网格必须绑定 mesh_assets 绝对路径；绑定后映射为 mesh 资产目录。
    def test_mesh_requires_an_absolute_mesh_assets_binding(self):
        value = program([box('sofa', kind='mesh', asset_id='sofa-1')])
        self.assertIn('/objects/0/asset_id', self.rejected(value))
        plan = simulation_plan(value, settings(mesh_assets={'sofa-1': '/srv/meshes/sofa'}),
                               capture_scope(value))
        self.assertEqual(plan['bodies'][0]['shape'], 'mesh')
        self.assertEqual(plan['bodies'][0]['asset'], '/srv/meshes/sofa')

    # 相机由世界位姿重建方位角、仰角与距离。
    def test_camera_view_reconstructs_azimuth_elevation_and_distance(self):
        camera = {'position': [3, 4, 5], 'target': [0, 0, 0]}
        view = camera_view(0, camera, [])
        distance = math.sqrt(50)
        self.assertAlmostEqual(view['distance'], distance)
        self.assertAlmostEqual(view['azimuth'], math.degrees(math.atan2(4, 3)))
        self.assertAlmostEqual(view['elevation'], math.degrees(math.asin(5 / distance)))

    # 未指定帧时按 ceil(steps/target) 自适应采样并强制包含最终帧。
    def test_adaptive_capture_interval_and_final_frame(self):
        for steps, target, expected in ((240, 24, 10), (100, 24, 5), (5, 24, 1)):
            with self.subTest(steps=steps):
                value = program([box()])
                plan = simulation_plan(value, settings(steps=steps, target_frames=target),
                                       capture_scope(value))
                self.assertEqual(plan['capture']['every'], expected)
                self.assertEqual(plan['capture']['frames'], [steps])
                self.assertEqual(plan['capture']['target_frames'], target)

    # 显式帧覆盖间隔并去重排序；every 覆盖自适应计算。
    def test_explicit_frames_and_interval_override(self):
        value = program([box()])
        scope = capture_scope(value, {'frames': [3, 1, 3, 2]})
        plan = simulation_plan(value, settings(steps=10), scope)
        self.assertEqual(plan['capture']['frames'], [1, 2, 3])
        self.assertIsNone(plan['capture']['every'])
        scope = capture_scope(value, {'every': 4})
        plan = simulation_plan(value, settings(steps=10), scope)
        self.assertEqual(plan['capture']['every'], 4)
        self.assertEqual(plan['capture']['frames'], [10])

    # 场景导出在 v2 中没有对应产物，必须显式拒绝。
    def test_scene_export_is_rejected(self):
        value = program([box()])
        scope = capture_scope(value, {'export_scene': True})
        self.assertIn('/capture_options/export_scene', self.rejected(value, None, scope))

    # 灯光仅映射环境穹顶与矩形面光，其他灯型明确拒绝。
    def test_lighting_maps_environment_and_area_lights(self):
        value = program([box()])
        value['lights'] = [
            {'id': 'dome', 'kind': 'environment', 'color': [1, 1, 1], 'intensity': 1},
            {'id': 'a', 'kind': 'area', 'position': [0, 0, 2], 'size': [1, .5],
             'color': [1, 1, 1], 'intensity': 5},
            {'id': 'b', 'kind': 'area', 'position': [1, 0, 2], 'size': [1, 1]}]
        plan = simulation_plan(value, settings(), capture_scope(value))
        self.assertEqual(plan['lighting']['dome']['color'], {'x': 1.0, 'y': 1.0, 'z': 1.0})
        self.assertEqual(plan['lighting']['key']['width'], 1.0)
        self.assertEqual(len(plan['lighting']['fills']), 1)
        value['lights'] = [{'id': 'spot', 'kind': 'point', 'position': [0, 0, 1],
                            'color': [1, 1, 1], 'intensity': 1}]
        self.assertIn('/lights/0/kind', self.rejected(value))

    # 单相机请求携带刚体、渲染方位与显式帧。
    def test_simulation_request_carries_bodies_render_and_capture(self):
        value = program([box()])
        plan = simulation_plan(value, settings(steps=6), capture_scope(value))
        request = simulation_request(plan, plan['views'][0])
        self.assertEqual(request['bodies'], plan['bodies'])
        self.assertEqual(request['steps'], 6)
        self.assertEqual(request['capture']['frames'], [6])
        self.assertEqual(request['render']['size'], 128)
        self.assertIn('azimuth', request['render'])
        self.assertEqual(request['render']['target'], {'x': 0.0, 'y': 0.0, 'z': 1.0})


if __name__ == '__main__':
    unittest.main()
