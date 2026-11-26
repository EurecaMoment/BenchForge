"""Verified artifact references and evidence frames from the v2 job contract."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
from PIL import Image

from capabilities.isaac_sim.evidence import (
    entities_from_job, frame_evidence, physics_summary, resolve_artifact,
)


# 构造与下载清单同形的文件引用。
def file(identity, name, path):
    return {'artifact_id': identity, 'name': name,
            'relative_path': 'frames/000001/' + name, 'source_path': str(path)}


# 构造最小映射计划，含单个盒体实体与渲染视场。
def plan(dynamic=False):
    return {'entities': {'cube': {
                'id': 'cube', 'label': 'red cube', 'kind': 'box', 'dynamic': dynamic,
                'support': 'ground', 'center': [0.0, 0.0, 0.5], 'size': [1.0, 1.0, 1.0],
                'mass_kg': 1.0, 'yaw_deg': 0.0, 'body_ids': ['cube']}},
            'render': {'fov_degrees': 50.0, 'size': 64, 'spp': 8, 'seed': 0}}


class ArtifactReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    # artifact:// 句柄按身份解析，绝对路径按后缀解析，名称可精确匹配。
    def test_reference_forms_resolve_to_the_single_downloaded_file(self):
        path = self.root / 'depth.npy'
        path.write_bytes(b'depth')
        files = [file('depth-1', 'depth.npy', path)]
        self.assertEqual(resolve_artifact('artifact://depth-1', files), path)
        self.assertEqual(resolve_artifact('/srv/jobs/x/frames/000001/depth.npy', files), path)
        self.assertEqual(resolve_artifact('depth.npy', files), path)

    # 缺失或歧义引用必须报错，不能猜测服务端路径。
    def test_missing_or_ambiguous_references_are_rejected(self):
        first = self.root / 'a' / 'depth.npy'
        second = self.root / 'b' / 'depth.npy'
        first.parent.mkdir(parents=True)
        second.parent.mkdir(parents=True)
        first.write_bytes(b'a')
        second.write_bytes(b'b')
        duplicate = [file('a', 'depth.npy', first), file('b', 'depth.npy', second)]
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            resolve_artifact('depth.npy', duplicate)
        with self.assertRaisesRegex(ValueError, 'absent'):
            resolve_artifact('artifact://missing', duplicate)
        with self.assertRaisesRegex(ValueError, 'omitted'):
            resolve_artifact('', duplicate)


class EvidenceFrameTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / 'capture'
        self.output.mkdir()
        self.files, self.frame = self.write_frame(42)

    # 写入一帧 RGB/深度/语义/标签并登记为下载清单。
    def write_frame(self, step, size=64):
        directory = self.root / 'download'
        directory.mkdir(exist_ok=True)
        pixels = np.random.default_rng(step).integers(24, 232, (size, size, 3), dtype=np.uint8)
        image, depth, semantic, labels = (directory / 'image.png', directory / 'depth.npy',
                                          directory / 'semantic.npy', directory / 'labels.json')
        Image.fromarray(pixels).save(image)
        values = np.full((size, size), 2.0, dtype=np.float32)
        ids = np.zeros((size, size), dtype=np.uint32)
        values[10:30, 10:40] = 1.5
        ids[10:30, 10:40] = 1
        np.save(depth, values)
        np.save(semantic, ids)
        labels.write_text(json.dumps({'classes': {'1': 'red cube'},
                                      'instances': {'1': {'prim_path': '/World/Objects/cube'}},
                                      'depth_convention': 'optical_axis_z_m', 'backend': 'mock'}))
        files = [file('image', 'image.png', image), file('depth', 'depth.npy', depth),
                 file('semantic', 'semantic.npy', semantic), file('labels', 'labels.json', labels)]
        frame = {'frame': step, 'image': 'artifact://image', 'depth_m': 'artifact://depth',
                 'semantic_ids': 'artifact://semantic', 'semantic_labels': 'artifact://labels'}
        return files, frame

    # 最终帧使用规范名 view_{i}，过程帧使用 process_view_{i}_step_{step}。
    def test_canonical_and_process_frames_use_distinct_names(self):
        camera = {'position': [2, -2, 2], 'target': [0, 0, 1]}
        canonical = frame_evidence(self.frame, self.files, 0, camera, {}, plan(), self.output,
                                   canonical=True, physics_tick=42, dt=1 / 60)
        self.assertEqual(canonical['frame_id'], 'view_0')
        self.assertTrue((self.output / 'view_0.png').exists())
        self.assertTrue((self.output / 'view_0_depth.npy').exists())
        self.assertTrue((self.output / 'view_0_semantic.npy').exists())
        self.assertTrue((self.output / 'view_0_instance.npy').exists())
        self.assertEqual(canonical['camera']['fov_source'], 'configured_default')
        self.assertEqual(canonical['instance_source'], 'semantic_ids')
        process = frame_evidence(self.frame, self.files, 0, camera, {}, plan(), self.output,
                                 canonical=False, physics_tick=42, dt=1 / 60)
        self.assertEqual(process['frame_id'], 'process_view_0_step_000042')
        self.assertTrue((self.output / 'process_view_0_step_000042.png').exists())

    # 语义区域应生成对象掩码、深度中位数与二维包围盒。
    def test_semantic_region_produces_object_measurements(self):
        camera = {'position': [2, -2, 2], 'target': [0, 0, 1]}
        result = frame_evidence(self.frame, self.files, 0, camera, {}, plan(), self.output,
                                canonical=True, physics_tick=42, dt=1 / 60)
        self.assertEqual(len(result['objects']), 1)
        recorded = result['objects'][0]
        self.assertEqual(recorded['object_id'], 'cube')
        self.assertEqual(recorded['bbox_2d']['xyxy'], [10, 10, 40, 30])
        self.assertAlmostEqual(recorded['depth_z_median'], 1.5)

    # 最终刚体位姿转换为当前中心与世界包围盒。
    def test_entities_use_the_final_simulated_pose(self):
        job = {'bodies': [{'id': 'cube', 'pose_xyzw': [0.0, 0.0, 0.75, 0.0, 0.0, 0.0, 1.0]}]}
        entities = entities_from_job({}, job, plan())
        record = entities['cube']
        np.testing.assert_allclose(record['current_center'], [0.0, 0.0, 0.75])
        np.testing.assert_allclose(record['world_aabb']['min'], [-0.5, -0.5, 0.25])
        np.testing.assert_allclose(record['world_aabb']['max'], [0.5, 0.5, 1.25])
        self.assertEqual(record['static_body_semantics'], 'upstream_default')

    # 动态刚体不标注上游静态语义。
    def test_dynamic_entities_are_not_marked_static(self):
        job = {'bodies': [{'id': 'cube', 'pose_xyzw': [0, 0, 0.5, 0, 0, 0, 1]}]}
        self.assertIsNone(entities_from_job({}, job, plan(dynamic=True))['cube']['static_body_semantics'])

    # 缺失最终位姿必须报错，不能静默跳过实体。
    def test_missing_final_pose_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'omitted the final pose'):
            entities_from_job({}, {'bodies': []}, plan())

    # 物理摘要依据最终线速度/角速度阈值报告稳定状态。
    def test_physics_summary_reports_stability_from_velocities(self):
        still = {'bodies': [{'id': 'cube', 'velocity': [0.0, 0.0, 0.0, 0.0, 0.0, 0.0]}],
                 'physics_device': 'cpu', 'simulation_seconds': 3.0}
        summary = physics_summary(still, 180, 1 / 60, plan(dynamic=True))
        self.assertTrue(summary['stable'])
        self.assertEqual(summary['dynamic_bodies'], 1)
        moving = {'bodies': [{'id': 'cube', 'velocity': [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]}]}
        self.assertFalse(physics_summary(moving, 180, 1 / 60, plan())['stable'])
        self.assertEqual(summary['basis'], 'isaacsim_rpc.v1 final BodyState velocities after simulation')


if __name__ == '__main__':
    unittest.main()
