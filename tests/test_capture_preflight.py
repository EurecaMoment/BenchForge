"""Batch diagnostics compose current adapter rules without replacing evidence."""
import copy
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from capabilities.isaac_sim.authoring import compile_scene, UnsupportedScene
from capabilities.isaac_sim.scene_requirements import capture_requirement_issues
from capabilities.scene_authoring.validation.diagnostics import ScenePreflightError
from pipelines.scene_production.preflight import inspect_capture_program, read_capture_submission
from server.http_api import request_error_payload
from service_access.scene_service import ScenePreflightRejected, service_call
from test_scene_diagnostics import sample, object_record


class CapturePreflightTests(unittest.TestCase):
    def test_repair_can_hand_off_a_missing_prop_to_the_generation_stage(self):
        """修复计划保存待生成的物件，执行时仍须使用已实现网格通过最终预检。"""
        from pipelines.scene_production import repair
        program = sample()
        obj = program['objects'][0]
        (self.root / 'program.json').write_text(json.dumps(program), encoding='utf-8')
        generated = {**obj, 'kind': 'generated', 'generation': {'prompt': 'A complete stoneware jug'},
                     'asset_id': 'obsolete_mesh', 'asset_quality': {'semantic_review': 'old'},
                     'parts': [{'shape': 'box'}]}
        expected_generated = {key: value for key, value in generated.items()
                              if key not in {'asset_id', 'asset_quality', 'parts'}}
        proposal = {
            'replacements': [{'path': '/objects/0', 'value': generated}],
            'brief': {
                'name': 'Workshop', 'summary': 'Workshop with a jug on the floor.',
                'physical_assumptions': ['Stable ground'],
                'object_roles': [{'object_id': obj['id'], 'role': 'Water storage'}],
                'relations': [{'source': obj['id'], 'target': 'ground', 'type': 'supported_by',
                               'description': 'The base sits on the floor.'}],
            },
        }
        config = {'_workspace': str(self.root), '_session': 'session', 'artifacts': str(self.root),
                  'execution_mode': 'exploration'}
        row = {'id': 'source', 'name': 'Workshop', 'source_directory': str(self.root)}
        with (patch.object(repair, 'author_completion', return_value=proposal),
              patch.object(repair, 'review', return_value={'review_id': 'test'})):
            record = repair.revise(config, row, self.root / 'repairs')
        saved = json.loads(Path(record['program_path']).read_text(encoding='utf-8'))
        self.assertEqual(saved['objects'][0], expected_generated)
        self.assertTrue(record['admission']['passed'])
        self.assertIn('asset_not_realized', [issue['code'] for issue in record['admission']['issues']])
        final = inspect_capture_program(self.root, saved, policy={}, allow_generated=False)
        self.assertFalse(final['passed'])
        self.assertIn('source_geometry_unavailable', [issue['code'] for issue in final['issues']])

    # 创建相互独立的临时工作区，供程序与适配器批量预检。
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    # 验证同一候选的适配器错误与通用契约错误都进入完整报告。
    def test_adapter_and_contract_errors_are_collected_from_the_same_candidate(self):
        program = sample()
        program['objects'] = [
            object_record('bad_size', size=[0, 1, 1], appearance={'ior': 1.5}),
            object_record('cup', kind='mug', size=[.0924, .1272, .091], cast_shadows=False),
            object_record('shiny', appearance={'specular': .5}),
        ]
        program['render_environment'] = {'renderer': 'RaytracedLighting', 'exposure': 1}
        program['lights'] = [{'id': 'unsupported', 'kind': 'point', 'position': [0, 0, 1],
                              'color': [1, 1, 1], 'intensity': 10}]
        program['interactions'] = [{'id': 'move', 'action': 'apply_force', 'object_id': 'bad_size'},
                                   {'id': 'video', 'action': 'record_trajectory'}]
        original = copy.deepcopy(program)
        report = inspect_capture_program(self.root, program, policy={}, settings={})
        paths = {row['path'] for row in report['issues']}
        self.assertTrue({'/objects/0/size', '/objects/0/appearance/ior', '/objects/1/kind',
                         '/objects/1/cast_shadows', '/objects/2/appearance/specular',
                         '/render_environment/renderer', '/render_environment/exposure',
                         '/lights/0/kind', '/interactions/0', '/interactions/1'} <= paths)
        self.assertFalse(report['passed'])
        self.assertEqual(program, original)
        self.assertEqual(report['runtime_checks']['status'], 'not_evaluated')

    # 验证原生资源绑定和别名检查采用本次部署设置。
    def test_native_binding_and_alias_check_use_deployment_settings(self):
        program = sample()
        program['objects'] = [object_record('cup', kind='mesh', asset_id='mug',
                                            size=[.0924, .1272, .091])]
        missing = capture_requirement_issues(program, self.root, {})
        self.assertIn('/objects/0/kind', {row['path'] for row in missing})
        self.assertEqual(capture_requirement_issues(
            program, self.root, {'asset_sources': {'mug': {'asset_id': 'native-cup'}}}), [])

    # 验证编译器沿用同一批适配器约束，不放宽被预检拒绝的字段。
    def test_compiler_uses_the_same_adapter_batch_and_does_not_relax_it(self):
        program = sample()
        program['objects'][0]['appearance'] = {'ior': 1.5, 'specular': .5}
        program['render_environment'] = {'renderer': 'RaytracedLighting', 'exposure': 1}
        expected = capture_requirement_issues(program, self.root, {})
        with self.assertRaises(UnsupportedScene) as caught:
            compile_scene(program, self.root, {})
        self.assertEqual(caught.exception.issues, expected)
        self.assertEqual(len(expected), 4)

    # 验证无效提交保存两类完整诊断，但不会创建队列任务。
    def test_submission_saves_adapter_and_contract_diagnostics_without_queueing(self):
        workspace = self.root / 'operator_workspaces' / 'test'
        workspace.mkdir(parents=True)
        source = workspace / 'scene.json'
        program = sample()
        program['objects'][0].update(size=[0, 1, 1], cast_shadows=False)
        source.write_text(json.dumps(program), encoding='utf-8')
        with self.assertRaises(ScenePreflightError) as caught:
            read_capture_submission(self.root, str(source), execution_mode='exploration')
        report = caught.exception.report
        self.assertTrue({'/objects/0/size', '/objects/0/cast_shadows'} <=
                        {row['path'] for row in report['issues']})
        self.assertEqual(json.loads(Path(report['report_path']).read_text(encoding='utf-8')), report)
        self.assertEqual(json.loads(Path(report['program_snapshot_path']).read_text(encoding='utf-8')), program)
        self.assertFalse(list(self.root.glob('sf_*')))

    # 验证 HTTP 摘要限制不截断持久报告或客户端收到的完整预检异常。
    def test_http_summary_does_not_truncate_the_saved_report_or_client_error(self):
        program = sample()
        program['objects'] = [object_record(f'item_{i}', support=f'absent_{i}') for i in range(80)]
        report = inspect_capture_program(self.root, program, policy={})
        payload = request_error_payload(ScenePreflightError(report), 400)
        self.assertLessEqual(len(payload['detail']), 1000)
        self.assertEqual(payload['preflight'], report)
        body = json.dumps(payload).encode()
        with patch('service_access.scene_service.load_service_client_config', return_value={
                'execution_mode': 'exploration', 'service_url': 'http://fixture', 'operator_token': 'fixture'}), \
                patch('service_access.scene_service.post_service_json',
                      side_effect=HTTPError('http://fixture/capture', 400, 'bad', {}, BytesIO(body))):
            with self.assertRaises(ScenePreflightRejected) as caught:
                service_call('/capture', {}, 'config.json')
        self.assertEqual(caught.exception.preflight, report)
        self.assertIn('absent_79', str(caught.exception))

    # 验证非预检 HTTP 错误保留原状态和响应正文。
    def test_unrelated_http_error_keeps_its_status_and_body(self):
        body = b'upstream unavailable'
        with patch('service_access.scene_service.load_service_client_config', return_value={
                'execution_mode': 'exploration', 'service_url': 'http://fixture', 'operator_token': 'fixture'}), \
                patch('service_access.scene_service.post_service_json',
                      side_effect=HTTPError('http://fixture/capture', 503, 'unavailable', {}, BytesIO(body))):
            with self.assertRaises(HTTPError) as caught:
                service_call('/capture', {}, 'config.json')
        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(caught.exception.read(), body)

    # 验证批量校验保留主原生资源身份及阶段要求。
    def test_main_native_identity_and_stage_requirements_survive_batch_validation(self):
        program = sample()
        design = {'id': 'design', 'name': 'Robot scene', 'summary': 'A recognizable robot on the floor',
                  'hard_requirements': ['Native robot', '100 trajectories'],
                  'requirement_stages': ['S2', 'S3'], 'physical_assumptions': ['Declared synthetic materials'],
                  'object_roles': [{'object_id': 'unknown_object', 'role': 'robot', 'representation': 'articulated_robot'}],
                  'relations': [{'source': 'unknown_object', 'type': 'supported_by', 'target': 'ground',
                                 'description': 'robot stands on the floor'}]}
        plan = self.root / 'plan.json'
        plan.write_text(json.dumps({'schema': 'benchforge.assessment/v1',
                                    'scenes': {'designs': [design]}}), encoding='utf-8')
        program['provenance'] = {'sources': [{'kind': 'scene_plan', 'path': str(plan),
                                             'scene_design_id': 'design'}]}
        report = inspect_capture_program(self.root, program, policy={'enforce_realism': True})
        self.assertIn('required_native_asset', {row['code'] for row in report['issues']})
        self.assertEqual(report['scene_brief']['requirement_stages'], ['S2', 'S3'])
        design['requirement_stages'] = ['S3']
        plan.write_text(json.dumps({'schema': 'benchforge.assessment/v1',
                                    'scenes': {'designs': [design]}}), encoding='utf-8')
        report = inspect_capture_program(self.root, program, policy={'enforce_realism': True})
        self.assertFalse(report['passed'])
        self.assertTrue(any('each hard requirement' in row['message'] for row in report['issues']))

    # 验证异常计划角色不使预检崩溃，也不遮蔽其他对象的问题。
    def test_malformed_plan_roles_do_not_crash_or_hide_other_objects(self):
        program = sample()
        program['objects'][0]['size'] = [0, 1, 1]
        plan = self.root / 'plan.json'
        program['provenance'] = {'sources': [{'kind': 'scene_plan', 'path': str(plan),
                                             'scene_design_id': 'design'}]}
        for roles in ([None], [0], {'id': []}, [{'representation': []}],
                      [{'representation': 'native_asset', 'object_id': []}]):
            with self.subTest(roles=roles):
                plan.write_text(json.dumps({'schema': 'benchforge.assessment/v1', 'scenes': {
                    'designs': [{'id': 'design', 'object_roles': roles}]}}), encoding='utf-8')
                report = inspect_capture_program(self.root, program, policy={'enforce_realism': True})
                self.assertFalse(report['passed'])
                self.assertIn('scene_plan', {row['code'] for row in report['issues']})
                self.assertIn('/objects/0/size', {row['path'] for row in report['issues']})
