"""Static admission and live Health checks for the isaacsim_rpc.v1 service."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from capabilities.isaac_sim.capture_contract import (
    capture_service_issues, health_issues, inspect_live_capture_service,
    require_capture_service,
)
from capabilities.isaac_sim.scene_requirements import UnsupportedScene
from pipelines.scene_production.preflight import inspect_capture_program
from capabilities.isaac_sim.mock_grpc_service import (
    MockSimulationService, serve_mock, simulation_config,
)
from test_scene_diagnostics import sample, object_record


# 取 mock 端点的 provider 设置，供只读 Health 检查复用。
def provider_settings(port, **options):
    return simulation_config(port, **options)['providers']['IsaacSim4A100']


class StaticCaptureContractTests(unittest.TestCase):
    # 一次收集不支持的对象、灯光、交互和曝光，且不修改原程序。
    def test_static_issues_are_collected_without_mutation(self):
        program = sample()
        program['objects'] = [object_record('tube', kind='cylinder')]
        program['lights'] = [{'id': 'spot', 'kind': 'point', 'position': [0, 0, 1],
                              'color': [1, 1, 1], 'intensity': 1}]
        program['interactions'] = [{'id': 'push', 'action': 'apply_force', 'object_id': 'tube'}]
        program['render_environment'] = {'exposure': 1}
        original = {key: list(value) if isinstance(value, list) else value
                    for key, value in program.items()}
        issues = capture_service_issues(program, provider_settings(18500))
        paths = {row['path'] for row in issues}
        self.assertTrue({'/objects/0/kind', '/lights/0/kind', '/interactions/0',
                         '/render_environment/exposure'} <= paths)
        self.assertEqual({row['basis'] for row in issues}, {'isaacsim_rpc.v1'})
        self.assertTrue(all(row['blocking'] for row in issues))
        self.assertEqual(program['objects'][0]['kind'], 'cylinder')
        self.assertNotIn('mutated', program)

    # 网格绑定来自 mesh_assets 的绝对服务端目录，绑定时不再报错。
    def test_mesh_binding_uses_mesh_assets_settings(self):
        program = sample()
        program['objects'] = [object_record('sofa', kind='mesh', asset_id='sofa-1')]
        self.assertIn('/objects/0/asset_id',
                      {row['path'] for row in capture_service_issues(program, provider_settings(18500))})
        bound = provider_settings(
            18500, mesh_assets={'sofa-1': os.path.abspath(os.sep + 'srv/meshes/sofa')})
        self.assertEqual(capture_service_issues(program, bound), [])

    # require_capture_service 在存在阻断问题时抛出带结构化字段的异常。
    def test_require_capture_service_raises_with_structured_issues(self):
        program = sample()
        program['objects'] = [object_record('tube', kind='cylinder')]
        with self.assertRaises(UnsupportedScene) as caught:
            require_capture_service(program, provider_settings(18500))
        self.assertTrue(caught.exception.issues)
        self.assertEqual(caught.exception.issues[0]['code'], 'unsupported_capture_requirement')

    # Health 状态非 ready 或契约版本不符都归为服务侧阻断。
    def test_health_issues_flag_unready_and_contract_mismatch(self):
        ready = {'status': 'ready', 'api_version': 'isaacsim_rpc.v1.SimulationService'}
        self.assertEqual(health_issues(ready), [])
        unready = health_issues({'status': 'initializing', 'api_version': 'isaacsim_rpc.v1'})
        self.assertEqual([row['code'] for row in unready], ['service_unavailable'])
        mismatch = health_issues({'status': 'ready', 'api_version': 'a100sim.v1'})
        self.assertEqual([row['code'] for row in mismatch], ['service_contract_mismatch'])
        self.assertFalse(mismatch[0]['retry_unchanged'])


class LiveCaptureContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server, cls.port, cls.servicer = serve_mock(MockSimulationService(), port=18500,
                                                        fallback=True)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop(0)
        cls.servicer.cleanup()

    def setUp(self):
        self.servicer.reset()

    # 合规服务与合法程序返回空问题列表。
    def test_inspect_live_accepts_a_healthy_v2_service(self):
        self.assertEqual(inspect_live_capture_service(sample(), provider_settings(self.port)), [])

    # 服务契约版本不符时报告部署问题而非场景问题。
    def test_inspect_live_reports_contract_mismatch(self):
        self.servicer.api_version = 'a100sim.v1.Simulator'
        issues = inspect_live_capture_service(sample(), provider_settings(self.port))
        self.assertEqual([row['code'] for row in issues], ['service_contract_mismatch'])

    # 无法连接端点时报告 service_unavailable 并保留可重试标记。
    def test_inspect_live_reports_unreachable_service(self):
        with TemporaryDirectory():
            issues = inspect_live_capture_service(
                sample(), provider_settings(18599, timeout_seconds=1))
        self.assertEqual([row['code'] for row in issues], ['service_unavailable'])
        self.assertIn('health check failed', issues[0]['message'])

    # 预检把实时服务问题并入完整静态诊断，且不声称执行过仿真。
    def test_preflight_merges_live_service_issues_without_simulating(self):
        issue = {'code': 'service_contract_mismatch', 'path': '', 'message': 'wrong service',
                 'basis': 'isaacsim_rpc.v1', 'severity': 'error', 'blocking': True,
                 'repair_owner': 'service_configuration_or_adapter', 'retry_unchanged': False}
        with TemporaryDirectory() as temporary, patch(
                'capabilities.isaac_sim.capture_contract.inspect_live_capture_service',
                return_value=[issue]):
            report = inspect_capture_program(Path(temporary), sample(), policy={},
                                             settings=provider_settings(18500))
        self.assertFalse(report['passed'])
        self.assertIn('service_contract_mismatch', [row['code'] for row in report['issues']])
        self.assertEqual(report['service_contract']['status'], 'incompatible')
        self.assertFalse(report['service_contract']['simulation_executed'])
        self.assertIn('geometry_preview', report)


if __name__ == '__main__':
    unittest.main()
