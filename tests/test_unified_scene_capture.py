"""Exercise the shared S2 entry point and the immutable snapshot/native route decision."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

import grpc
import numpy as np
from PIL import Image
from pxr import Usd, UsdGeom, UsdPhysics

from capabilities.capture.capture_scope import capture_scope
from capabilities.isaac_sim.capture_stage import execute_capture
from capabilities.isaac_sim.grpc import simulation_pb2 as rpc
from capabilities.isaac_sim.grpc.unified_transport import PROTOCOL
from capabilities.isaac_sim.grpc.tasks import request_id
from capabilities.isaac_sim.scene_requirements import UnsupportedScene
from capabilities.isaac_sim.unified_capture import select_snapshot_plan
from capabilities.isaac_sim.unified_mapping import simulation_request
from pipelines.scene_production.preflight import inspect_capture_program
from service_access.configuration import isaac_unified_settings, validate_providers
from test_scene_diagnostics import sample


def health(*, native=False):
    return rpc.HealthResponse(status='ready', engine='A100' if native else 'RTX', native_rtx=not native,
        max_views=32, max_image_size=2048, max_samples_per_pixel=1024,
        capabilities=['multiview_frames', 'metric_depth', 'semantic_masks', 'instance_ids',
                      'registered_mesh_npz'] + (['native_operations'] if native else []))


def job_outputs(directory, program, size=64):
    job = rpc.Job(id='fixture-job', state='succeeded', steps=180, dt=1/60, native_rtx=True,
                  phase='succeeded', frames_done=1, frames_total=1)
    job.render.renderer = 'RayTracedLighting'
    job.render.backend = 'native_rtx'
    job.render.rt_subframes = 32
    job.bodies.add(id=program['objects'][0]['id'], prim_path='/World/Body',
                   pose_xyzw=[0, 0, 0, 0, 0, 0, 1], velocity=[0] * 6)
    directory.mkdir()
    stage = Usd.Stage.CreateNew(str(directory / 'scene.usdc'))
    UsdGeom.Xform.Define(stage, '/World/Body')
    cube = UsdGeom.Cube.Define(stage, '/World/Body/Visual')
    UsdPhysics.CollisionAPI.Apply(cube.GetPrim())
    stage.GetRootLayer().Save()
    stage = None
    job.outputs.scene = str(directory / 'scene.usdc')
    snapshots = {}
    for index in range(len(program['cameras'])):
        name = 'view_' + str(index)
        view = directory / 'frames' / 'frame_000180' / 'views' / name
        view.mkdir(parents=True)
        labels = np.zeros((size, size), dtype=np.uint32)
        labels[5:25, 5:25] = 2
        Image.fromarray(np.asarray(labels * 80, dtype=np.uint8)).convert('RGB').save(view / 'render.png')
        for key, array in [('depth_m', labels.astype(np.float32)), ('instance_ids', labels), ('semantic_ids', labels)]:
            np.save(view / (key + '.npy'), array)
        for key, data in [('semantic_labels', {'idToLabels': {'2': 'cube'}}),
                          ('instance_labels', {'idToLabels': {'2': '/World/Body/Visual'}}),
                          ('annotations', {'depth_convention': 'metric camera-forward distance',
                                           'frame': 180, 'view_id': name}),
                          ('camera', {'frame': 180, 'id': name, 'width': size, 'height': size,
                                      'K': np.eye(3).tolist(), 'usd_camera_to_world': np.eye(4).tolist(),
                                      'fov_y_degrees': 55.0 + index})]:
            (view / (key + '.json')).write_text(json.dumps(data))
        snapshots[name] = {path.stem: str(path) for path in view.iterdir()}
        snapshots[name]['image'] = snapshots[name].pop('render')
    job.outputs.simulation_json = str(directory / 'simulation.json')
    Path(job.outputs.simulation_json).write_text(json.dumps({'frames': [{'frame': 180, 'views': snapshots}]}))
    files = [{'name': path.name, 'artifact_id': path.relative_to(directory).as_posix(),
              'relative_path': path.relative_to(directory).as_posix(), 'source_path': str(path)}
             for path in directory.rglob('*') if path.is_file()]
    return job, files


class UnifiedSceneCaptureTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.directory = self.root / 'revision_0'
        self.directory.mkdir()
        self.program = sample()
        self.program['cameras'].append({'position': [-1, -2, 2], 'target': [0, 0, 0]})
        self.settings = {'grpc': {'host': 'fixture'}, 'image_size': 64, 'samples_per_pixel': 32}
        self.config = {'providers': {'IsaacSim4A100': self.settings}}
        self.store = Mock(root=self.root)
        self.store.directory.return_value = self.directory
        self.task = {'id': 'scene', 'unit': {'revision': 0, 'intent': {}}}
        self.client = Mock(endpoint='fixture:50051', timeout=1)
        self.client.health.return_value = health()

    def execute(self):
        (self.directory / 'program.json').write_text(json.dumps(self.program))
        execute_capture(self.store, self.task, Event(), config=self.config, client=self.client)

    def test_default_multiview_capture_publishes_job_usd_instances_and_calibration(self):
        job, files = job_outputs(self.root / 'service', self.program)
        self.client.submit.return_value = rpc.JobHandle(id=job.id)
        self.client.get.return_value = job
        def wait(current, **options):
            options['on_status'](current)
            return current
        self.client.wait.side_effect = wait
        self.client.download.return_value = files
        self.execute()
        request = self.client.submit.call_args.args[0]
        self.assertFalse(request['ground'])
        self.assertEqual([v['id'] for v in request['render']['views']], ['view_0', 'view_1'])
        self.assertEqual(request['render']['size'], 64)
        attempt = self.directory / 'isaac_sim/attempt_0'
        evidence = json.loads((attempt / 'capture/evidence.json').read_text())
        self.assertEqual(evidence['mode'], 'shared_snapshot')
        self.assertEqual(evidence['job_ids'], [job.id])
        self.assertEqual([v['objects'][0]['mask']['area_px'] for v in evidence['frames']], [400, 400])
        self.assertEqual(evidence['render_settings']['resolution'], [64, 64])
        self.assertEqual(evidence['render_settings']['rt_subframes'], 32)
        self.assertEqual(evidence['frames'][1]['camera']['depth_convention'], 'metric camera-forward distance')
        entity = evidence['entities'][self.program['objects'][0]['id']]
        self.assertEqual(entity['collision']['paths'], ['/World/Body/Visual'])
        self.assertEqual(entity['world_aabb']['max'], [1, 1, 1])
        stage = Usd.Stage.Open(str(attempt / 'capture/scene.usda'))
        self.assertTrue(stage.GetPrimAtPath('/World/Body/Visual'))
        stage = None
        package = Usd.Stage.Open(str(attempt / 'capture/scene.usdz'))
        self.assertTrue(package.GetPrimAtPath('/World/Body/Visual'))
        package = None
        self.assertEqual(json.loads((self.directory / 'progress.json').read_text())['job_id'], job.id)
        self.client.ack.assert_called_once_with(job.id)
        self.client.health.side_effect = AssertionError('must resume frozen job without reselection')
        self.execute()
        self.client.submit.assert_called_once()

    def test_native_operations_required_for_authored_trellis_and_physics(self):
        self.program['objects'][0].update(kind='mesh', asset_id='trellis', physics={'static_friction': .6})
        self.settings['mesh_assets'] = {'trellis': {'asset_id': 'trellis', 'bounds_m': [[0, 0, 0], [1, 1, .1]]}}
        scope = capture_scope(self.program)
        self.assertIsNone(select_snapshot_plan(self.program, self.settings, scope, health(native=True)))
        with self.assertRaisesRegex(UnsupportedScene, 'RTX.*persistent scene operations'):
            self.execute()
        self.client.submit.assert_not_called()
        self.client.upload.assert_not_called()
        self.store.reserve_capture.assert_not_called()

    def test_static_and_live_preflight_accept_explicit_rtx_renderer(self):
        self.program['render_environment'] = {'renderer': 'RaytracedLighting'}
        with patch('capabilities.isaac_sim.capture_contract.capture_service_client', return_value=self.client):
            report = inspect_capture_program(self.root, self.program, policy={}, settings=self.settings)
        self.assertTrue(report['passed'], report['issues'])
        self.client.health.assert_called_once()
        self.client.call.assert_not_called()
        with self.assertRaisesRegex(UnsupportedScene, 'requires RaytracedLighting'):
            select_snapshot_plan(self.program, self.settings, capture_scope(self.program), health(native=True))

    def test_missing_instances_or_excess_resolution_never_submits_a_downgrade(self):
        rtx = health()
        rtx.capabilities.remove('instance_ids')
        self.client.health.return_value = rtx
        with self.assertRaisesRegex(UnsupportedScene, 'instance_ids'):
            self.execute()
        rtx.max_image_size = 32
        with self.assertRaisesRegex(UnsupportedScene, '32, requested 64'):
            self.execute()
        self.client.submit.assert_not_called()

    def test_discovery_failure_never_calls_another_wire(self):
        class MissingHealth(grpc.RpcError):
            def code(self):
                return grpc.StatusCode.UNIMPLEMENTED
            def details(self):
                return 'method not found'
        self.client.health.side_effect = MissingHealth()
        with self.assertRaisesRegex(UnsupportedScene, 'SimulationService.Health failed: UNIMPLEMENTED'):
            self.execute()
        self.client.call.assert_not_called()
        self.client.submit.assert_not_called()

    def test_frozen_legacy_native_attempt_keeps_its_own_endpoint_and_input_document(self):
        saved = {'protocol': 'a100sim.v1.Simulator', 'settings': self.settings.copy(),
                 'program': self.program, 'capture_scope': capture_scope(self.program),
                 'capture_token': request_id(str(self.directory), 'capture-0'), 'mesh_encoding': 'typed_usdc'}
        path = self.directory / 'isaac_sim/attempt_0/capture.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(saved))
        self.settings['unified_capture'] = {'grpc': {'host': 'historical-snapshot-endpoint'}}
        with patch('capabilities.isaac_sim.capture_stage._compile_for_task', side_effect=InterruptedError) as compile_scene:
            with self.assertRaises(InterruptedError):
                self.execute()
        self.assertEqual(compile_scene.call_args.args[-1], saved['settings'])
        self.assertEqual(json.loads(path.read_text()), saved)
        self.client.health.assert_not_called()

    def test_older_frozen_snapshot_retains_its_original_physics_and_request(self):
        self.task['unit']['intent']['capture_options'] = {'export_scene': False}
        self.settings['unified_capture'] = {'grpc': {'host': 'snapshot'}}
        scope = capture_scope(self.program, {'export_scene': False})
        plan = select_snapshot_plan(self.program, self.settings, scope, health())
        plan['ground'] = True
        saved = {'protocol': PROTOCOL, 'settings': {**self.settings, **self.settings['unified_capture'],
                    'render': {'image_size': 64, 'samples_per_pixel': 32}},
                 'program': self.program, 'capture_scope': scope, 'plan': plan,
                 'capture_token': request_id(str(self.directory), 'capture-0')}
        path = self.directory / 'isaac_sim/attempt_0/capture.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(saved))
        self.client.submit.side_effect = InterruptedError
        with self.assertRaises(InterruptedError):
            self.execute()
        self.assertEqual(self.client.submit.call_args.args[0], simulation_request(plan))
        self.client.health.assert_not_called()
        self.assertEqual(json.loads(path.read_text()), saved)

    def test_process_frames_keep_explicit_steps_and_periodic_sampling(self):
        selection = capture_scope(self.program, {'frames': [5, 5, 20], 'every': 30})
        plan = select_snapshot_plan(self.program, self.settings, selection, health())
        self.assertEqual(simulation_request(plan)['capture'], {'frames': [5, 20], 'every': 30})
        self.program['objects'][0]['physics'] = {'static_friction': .6}
        with self.assertRaisesRegex(UnsupportedScene, 'Process frames require'):
            select_snapshot_plan(self.program, self.settings, selection, health(native=True))

    def test_legacy_nested_settings_resolve_to_one_endpoint(self):
        settings = {**self.settings, 'unified_capture': {'grpc': {'host': 'snapshot'},
            'mesh_assets': {'m': {'asset_id': 'm', 'bounds_m': [[0, 0, 0], [1, 1, 1]]}}}}
        validate_providers({'providers': {'IsaacSim4A100': settings}})
        resolved = isaac_unified_settings(settings)
        self.assertEqual(resolved['grpc'], {'host': 'snapshot'})
        self.assertIn('m', resolved['mesh_assets'])
        self.assertNotIn('unified_capture', resolved)
