"""Native SceneProgram handles retain scope, protocol and acceptance boundaries."""

from copy import deepcopy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from capabilities.isaac_sim.authoring import compile_scene
from capabilities.isaac_sim.capture_stage import _compile_for_task, _native_imports
from capabilities.isaac_sim.scene_requirements import (
    UnsupportedScene, capture_requirement_issues, validate_scene_requirements,
)
from capabilities.scene_authoring.catalog.native import ASSETS
from capabilities.scene_authoring.catalog.native_reference import validate_native_reference
from capabilities.scene_authoring.validation.program import program_contract_issues, validate_program
from database.access import AccessScope
from pipelines.scene_production.lifecycle import SceneLifecycle
from pipelines.scene_production.preflight import inspect_capture_program, read_capture_submission
from service_access.access_scope import TrustedAccess, access_context, current_access


# 构造使用显式上传原生机器人引用的场景夹具。
def scene():
    return {'schema': 'benchforge.scene/v2', 'scene_id': 'native_test', 'title': 'Native uploaded robot',
            'objects': [{'id': 'robot', 'label': 'Uploaded robot', 'kind': 'native',
                         'size': [1, 2, 3], 'xy': [0, 0], 'support': 'ground', 'base_z': 0,
                         'dynamic': False, 'mass_kg': 0,
                         'native_asset': {'asset_id': 'upload-robot', 'relative_path': 'robot.usdz',
                                          'size': [1, 2, 3], 'source_up_axis': 'Z'}}],
            'cameras': [{'position': [3, -4, 3], 'target': [0, 0, 1]}], 'assumptions': []}


class NativeReferenceTests(unittest.TestCase):
    # 创建临时工作区、仿真端点及可信访问身份，隔离原生引用归属测试。
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.settings = {'grpc': {'host': '127.0.0.1', 'port': 50051}}
        self.access = TrustedAccess(AccessScope('host', 'session-a', 'workspace-a', 'owner', 1, 1),
                                    str(self.root))

    # 验证原生对象必须声明安全的上传引用，且校验不改动静态资源目录。
    def test_contract_requires_explicit_safe_upload_reference(self):
        original_catalog = deepcopy(ASSETS)
        program = scene()
        validate_program(program)
        reference = program['objects'][0]['native_asset']
        invalid = [None, {}, {**reference, 'source': '/srv/private/robot.usd'},
                   {**reference, 'asset_id': '../other'}, {**reference, 'fix_base': 'true'},
                   {**reference, 'size': [1, True, 3]}, {**reference, 'size': [1, float('inf'), 3]},
                   {**reference, 'source_up_axis': 'X'}]
        invalid.extend({**reference, 'relative_path': path} for path in (
            '/home/private.usd', '../other.usd', 'a/../other.usd', 'C:\\private.usd',
            'https://host/robot.usd', 'a/%2e%2e/robot.usd', 'a//robot.usd'))
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_native_reference(value)
                candidate = deepcopy(program)
                candidate['objects'][0]['native_asset'] = value
                issues = program_contract_issues(candidate)
                self.assertIn('/objects/0/native_asset', {row['path'] for row in issues})
        candidate = deepcopy(program)
        candidate['objects'][0]['kind'] = 'box'
        self.assertIn('/objects/0/native_asset', {row['path'] for row in program_contract_issues(candidate)})
        self.assertEqual(ASSETS, original_catalog)

    # 验证已授权上传资源无需绑定静态目录也能通过预检。
    def test_owned_native_asset_preflights_without_static_catalog_binding(self):
        program = scene()
        with access_context(self.access), patch('capabilities.isaac_sim.resource_access.NativeResources') as owner:
            report = inspect_capture_program(self.root, program, settings=self.settings, policy={})
            self.assertTrue(report['passed'], report['issues'])
            owner.assert_called_once_with('127.0.0.1:50051')
            owner.return_value.require.assert_called_once_with('asset', 'upload-robot')
            self.assertEqual(report['runtime_checks']['status'], 'not_evaluated')

    # 验证所有适配入口在资源归属校验失败时一致拒绝访问。
    def test_ownership_failure_blocks_all_adapter_entry_points(self):
        program = scene()
        with access_context(self.access), patch('capabilities.isaac_sim.resource_access.NativeResources') as owner:
            owner.return_value.require.side_effect = PermissionError('Native asset is not granted to this conversation')
            issues = capture_requirement_issues(program, self.root, self.settings)
            self.assertEqual([row['path'] for row in issues], ['/objects/0/native_asset'])
            for call in (lambda: validate_scene_requirements(program, self.settings),
                         lambda: compile_scene(program, self.root, self.settings)):
                with self.assertRaises(UnsupportedScene):
                    call()
            report = inspect_capture_program(self.root, program, settings=self.settings, policy={})
            self.assertFalse(report['passed'])

    # 验证原生资源身份可满足角色声明，但不自动证明关节控制能力。
    def test_native_identity_satisfies_declared_role_without_certifying_articulation(self):
        program = scene()
        design = {'id': 'robot_design', 'name': 'Uploaded robot', 'summary': 'A real robot on the floor',
                  'hard_requirements': ['Use the uploaded native robot'], 'requirement_stages': ['S2'],
                  'physical_assumptions': ['Static position is a declared prior'],
                  'object_roles': [{'object_id': 'robot', 'role': 'robot', 'representation': 'articulated_robot'}],
                  'relations': [{'source': 'robot', 'type': 'supported_by', 'target': 'ground',
                                 'description': 'robot is placed on the floor'}]}
        plan = self.root / 'plan.json'
        plan.write_text(json.dumps({'schema': 'benchforge.assessment/v1', 'scenes': {'designs': [design]}}))
        program['provenance'] = {'sources': [{'kind': 'scene_plan', 'path': str(plan),
                                             'scene_design_id': 'robot_design'}]}
        report = inspect_capture_program(self.root, program, settings=self.settings, policy={'enforce_realism': True})
        self.assertTrue(report['passed'], report['issues'])
        self.assertEqual(report['runtime_checks']['status'], 'not_evaluated')
        program['objects'][0]['size'] = [9, 2, 3]
        report = inspect_capture_program(self.root, program, settings=self.settings, policy={'enforce_realism': True})
        self.assertIn('native_asset_scale', {row['code'] for row in report['issues']})

    # 验证直接提交在任何持久写入前核对原生资源所有权。
    def test_direct_admission_checks_native_ownership_before_persistence(self):
        config = self.root / 'models.json'
        config.write_text(json.dumps({'providers': {'IsaacSim4A100': self.settings}}))
        with (access_context(self.access), patch.dict(os.environ, {'BENCHFORGE_MODELS_CONFIG': str(config)}),
              patch('capabilities.isaac_sim.resource_access.NativeResources') as owner,
              patch('pipelines.scene_production.lifecycle.policy_for', return_value={})):
            owner.return_value.require.side_effect = PermissionError('different conversation')
            with self.assertRaises(UnsupportedScene):
                SceneLifecycle(self.root).admit([{'execution_mode': 'exploration'}], 'scene',
                                                [{'program': scene()}])
            owner.assert_called_once_with('127.0.0.1:50051')

    # 验证后台编译恢复原任务所有者，并拒绝猜测的资源句柄。
    def test_queued_compile_recovers_owner_and_denies_guessed_handles(self):
        store = SimpleNamespace(root=self.root, repo=SimpleNamespace(engine=object()))
        task = {'id': 'run-a.scene0'}
        with (patch('service_access.task_access.task_access', return_value=self.access) as recovered,
              patch('capabilities.isaac_sim.resource_access.NativeResources') as owner):
            owner.return_value.require.side_effect = PermissionError('different conversation')
            self.assertIsNone(current_access())
            with self.assertRaises(UnsupportedScene):
                _compile_for_task(store, task, scene(), self.settings)
            recovered.assert_called_once_with(store.repo.engine, task['id'])
            owner.return_value.require.assert_called_once_with('asset', 'upload-robot')
            self.assertIsNone(current_access())

    # 验证原生导入沿用现有 gRPC 契约并按源坐标轴解释尺寸。
    def test_native_import_uses_existing_grpc_contract_and_source_axis_dimensions(self):
        program = scene()
        obj = program['objects'][0]
        obj['size'] = [2, 6, 4]
        obj['native_asset'].update(source_up_axis='Y', fix_base=True)
        compiled = compile_scene(program, self.root, self.settings)
        self.assertEqual(compiled['imports'][0]['catalog']['size'], [1, 3, 2])
        self.assertNotIn('def Cube', compiled['usd'])
        requests = Mock()
        requests.extension.return_value = ({}, {'articulation_roots': ['/World/Scene/Objects/robot/base']})
        _native_imports(requests, 'owned-world', compiled)
        imported = requests.extension.call_args_list[0].args
        self.assertEqual(imported[1], 'asset.import')
        self.assertEqual(imported[2]['source'], {'asset_id': 'upload-robot', 'relative_path': 'robot.usdz'})
        self.assertEqual(imported[2]['source_up_axis'], 'Y')
        self.assertTrue(imported[2]['fix_base'])
        changes = requests.extension.call_args_list[1].args[2]['changes']
        self.assertEqual(changes[0]['scale'], [2, 2, 2])
        self.assertEqual(compiled['entities']['robot']['articulation_roots'], ['/World/Scene/Objects/robot/base'])

    # 验证带 asset 前缀的原生句柄不会被误当成生成网格。
    @unittest.skipUnless(os.name == 'posix', 'Confined submission read requires Linux descriptors')
    def test_native_handle_named_asset_prefix_is_not_a_generated_mesh(self):
        program = scene()
        program['objects'][0]['native_asset']['asset_id'] = 'asset_native_uploaded'
        source = self.root / 'scene.json'
        source.write_text(json.dumps(program))
        with (access_context(self.access), patch('capabilities.isaac_sim.resource_access.NativeResources') as owner,
              patch('capabilities.asset_generation.generation.GenerationTools.asset_path') as generated):
            result = read_capture_submission(self.root / 'private', 'scene.json',
                                             settings=self.settings, execution_mode='exploration')
            self.assertEqual(result['program'], program)
            generated.assert_not_called()
            owner.return_value.require.assert_called_once_with('asset', 'asset_native_uploaded')
