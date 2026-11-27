"""服务兼容性检查覆盖所有本体，在创建远端世界前批量定位参数错配。"""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

from google.protobuf.json_format import ParseDict
from capabilities.isaac_sim.capture_contract import (
    checked_service_issues, capture_service_issues, native_import_arguments, require_capture_service, unsupported_fields,
)
from capabilities.isaac_sim.capture_stage import execute_capture
from capabilities.isaac_sim.grpc import native_pb2 as pb
from capabilities.isaac_sim.grpc import simulation_pb2 as rpc
from capabilities.isaac_sim.scene_requirements import UnsupportedScene
from pipelines.scene_production.preflight import inspect_capture_program
from test_scene_diagnostics import sample


def capabilities():
    """提供开放字段的协议夹具，单个用例可按真实旧服务收窄导入合同。"""
    return {'capabilities': [{'name': 'extension.' + name, 'availability': 'AVAILABLE',
        'details': {'parameter_schema': {'type': 'object', 'additionalProperties': True}}}
        for name in ('asset.import', 'physics.step', 'world.observe', 'scene.inspect',
                     'scene.close', 'scene.capture', 'scene.export', 'scene.edit')]}


def compiled():
    """用不同名称和物理需求的本体复现同一接口问题，避免只为无人机特判。"""
    return {'usd': '#usda 1.0\n', 'imports': [
        {'object': {'id': name, 'label': name, 'dynamic': True, 'mass_kg': mass,
                    'physics': {'static_friction': .6, 'dynamic_friction': .5}},
         'source': {'asset_id': name, 'relative_path': 'robot.usda'},
         'path': '/World/Scene/Objects/' + name, 'catalog': {'source_up_axis': 'Z'}}
        for name, mass in (('drone', .25), ('dog', 15), ('arm', 7))]}


class CaptureServiceContractTests(unittest.TestCase):
    def test_old_schema_reports_all_native_objects_and_fields_without_mutation(self):
        """一次报告旧网关对每个本体拒绝的全部字段，不建议删除硬要求。"""
        catalog, scene = capabilities(), compiled()
        catalog['capabilities'][0]['details']['parameter_schema'] = {'additionalProperties': False,
            'properties': {key: {} for key in ('session_id', 'source', 'path', 'fix_base')}}
        original = deepcopy(scene)
        issues = capture_service_issues(catalog, scene, {'view_indices': [0], 'scene_export_requested': True})
        self.assertEqual(len(issues), 3)
        self.assertEqual({row['path'] for row in issues}, {'/objects/drone', '/objects/dog', '/objects/arm'})
        for row in issues:
            self.assertEqual(set(row['unsupported_fields']), {'/mass_kg', '/physics', '/source_up_axis'})
            self.assertFalse(row['retry_unchanged'])
            self.assertEqual(row['repair_owner'], 'service_configuration_or_adapter')
        self.assertEqual(scene, original)

    def test_open_metadata_and_unknown_schema_keywords_do_not_add_restrictions(self):
        """开放的物理元数据继续透传，适配器不擅自收窄服务端声明。"""
        schema = {'additionalProperties': False, 'properties': {'physics': {'additionalProperties': True}}}
        self.assertEqual(unsupported_fields(schema, {'physics': {'custom_metadata': {}}}), [])
        self.assertEqual(unsupported_fields({}, {'future_parameter': {}}), [])
        self.assertEqual(unsupported_fields({'properties': {'physics': {'additionalProperties': False,
            'properties': {'mass': {}}}}}, {'physics': {'friction': {}}}), ['/physics/friction'])
        require_capture_service(capabilities(), compiled(), {'view_indices': [0], 'scene_export_requested': True})
        request = native_import_arguments(compiled()['imports'][0], 'owned-session')
        self.assertEqual(request['mass_kg'], .25)
        self.assertEqual(request['source_up_axis'], 'Z')
        self.assertEqual(request['physics']['static_friction'], .6)

    def test_optional_render_and_export_do_not_block_physics_only_work(self):
        """未请求的输出能力不构成阻断；实际请求时才逐项报告缺失接口。"""
        catalog = capabilities()
        catalog['capabilities'] = [row for row in catalog['capabilities']
                                   if row['name'] not in ('extension.scene.capture', 'extension.scene.export')]
        scene = {'imports': []}
        self.assertEqual(capture_service_issues(catalog, scene,
            {'view_indices': [], 'scene_export_requested': False}), [])
        issues = capture_service_issues(catalog, scene, {'view_indices': [0], 'scene_export_requested': True})
        self.assertEqual({row['operation'] for row in issues}, {'extension.scene.capture', 'extension.scene.export'})

    def test_busy_worker_recovers_before_any_mutation(self):
        ready = capabilities()
        busy = deepcopy(ready)
        busy['capabilities'][0].update(availability='UNAVAILABLE', reason='Worker health checks timed out')
        client = Mock()
        client.call.side_effect = [ParseDict(value, pb.Capabilities()) for value in (busy, ready)]
        waiting = Mock()
        stop = Mock(spec=['is_set'])
        stop.is_set.return_value = False
        with patch('capabilities.isaac_sim.capture_contract.time.sleep') as pause:
            issues = checked_service_issues(client, compiled(), {'view_indices': [0], 'scene_export_requested': True},
                                           stop=stop, delays=(1,), on_wait=waiting)
            pause.assert_called_once_with(1)
        self.assertEqual(issues, [])
        self.assertEqual(client.call.call_count, 2)
        waiting.assert_called_once()
        client.submit.assert_not_called()
        client.upload.assert_not_called()

    def test_closed_edit_items_report_all_fields_without_guessing_union_branches(self):
        """编辑数组的明确拒绝一次报告，含值域分支的组合合同交由服务校验。"""
        catalog = capabilities()
        edit = next(row for row in catalog['capabilities'] if row['name'] == 'extension.scene.edit')
        edit['details']['parameter_schema'] = {'properties': {'changes': {'items': {
            'additionalProperties': False, 'properties': {'op': {}, 'path': {}}}}}}
        issues = capture_service_issues(catalog, compiled(), {'view_indices': [], 'scene_export_requested': False})
        self.assertEqual(len(issues), 3)
        self.assertIn('/changes/0/translation', issues[0]['unsupported_fields'])
        self.assertIn('/changes/1/attribute', issues[0]['unsupported_fields'])
        edit['details']['parameter_schema']['properties']['changes']['items'] = {
            'oneOf': [{'additionalProperties': False, 'properties': {'op': {}}}, {}]}
        self.assertEqual(capture_service_issues(catalog, compiled(),
            {'view_indices': [], 'scene_export_requested': False}), [])

    def test_incompatible_capture_stops_before_upload_or_remote_submission(self):
        """实际采集入口必须在上传前完成接口核对，不能留下失败世界或重复作业。"""
        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / 'program.json').write_text(json.dumps(sample()), encoding='utf-8')
            store = Mock(root=directory)
            store.directory.return_value = directory
            client = Mock()
            client.health.return_value = rpc.HealthResponse(status='ready', engine='a100sim',
                capabilities=['native_operations'])
            client.call.return_value = ParseDict({'capabilities': []}, pb.Capabilities())
            task = {'id': 'capture.scene0', 'unit': {'revision': 0, 'intent': {}}}
            with (patch('capabilities.isaac_sim.capture_stage._compile_for_task', return_value=compiled()),
                  patch('capabilities.isaac_sim.capture_stage.record_progress'),
                  self.assertRaises(UnsupportedScene) as rejected):
                execute_capture(store, task, Event(), config={}, client=client)
            self.assertGreater(len(rejected.exception.issues), 3)
            client.upload.assert_not_called()
            client.submit.assert_not_called()
            store.reserve_capture.assert_not_called()
            store.publish_capture.assert_not_called()

    def test_preflight_reports_live_service_failure_without_claiming_simulation(self):
        """只读预检把服务错配作为部署问题呈现，同时保留静态几何诊断。"""
        with TemporaryDirectory() as temporary:
            issue = {'code': 'service_contract_mismatch', 'path': '', 'message': 'wrong service',
                     'basis': 'live_isaac_sim_schema', 'severity': 'error', 'blocking': True,
                     'repair_owner': 'service_configuration_or_adapter'}
            with patch('capabilities.isaac_sim.capture_contract.inspect_live_capture_service', return_value=[issue]):
                report = inspect_capture_program(Path(temporary), sample(), policy={},
                    settings={'grpc': {'host': 'fixture', 'port': 50051}})
            self.assertFalse(report['passed'])
            self.assertIn('service_contract_mismatch', [row['code'] for row in report['issues']])
            self.assertEqual(report['service_contract']['status'], 'incompatible')
            self.assertFalse(report['service_contract']['simulation_executed'])
            self.assertIn('geometry_preview', report)
