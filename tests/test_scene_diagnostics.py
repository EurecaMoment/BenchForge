import copy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from capabilities.scene_authoring.validation.program import program_contract_issues, validate_program
from capabilities.scene_authoring.validation.contract_diagnostics import ProgramValidationError
from capabilities.scene_authoring.geometry.preview import compact_preview, mesh_fit_preview
from capabilities.scene_authoring.program_submission import inspect_program_submission, read_program_submission
from capabilities.capture.operator_evidence import evidence
from capabilities.scene_authoring.catalog.operators import catalog_response
from capabilities.scene_authoring.validation.acceptance import VISUAL_CHECKS, admit_program, capture_acceptance
from capabilities.scene_authoring.validation.preflight import inspect_program, ScenePreflightError
# 构造最小合法新颖场景，供独立错误组合与预检报告测试。
def sample():
    return {'schema':'benchforge.scene/v1','scene_id':'test','title':'Novel arrangement',
            'objects':[{'id':'unknown_object','label':'A novel object','kind':'box','size':[1,1,.1],
                        'color':[.2,.3,.4],'xy':[0,0],'support':'ground','base_z':0,'yaw_deg':0,
                        'dynamic':False,'mass_kg':1,'parts':[]}],
            'cameras':[{'position':[1,-2,2],'target':[0,0,0]}],'assumptions':['synthetic geometry']}


# 复制标准对象并应用显式覆盖，便于构造单个问题候选。
def object_record(name, **changes):
    return {**sample()['objects'][0], 'id': name, **changes}


class SceneDiagnosticsTests(unittest.TestCase):
    # 创建独立工作区与默认场景程序。
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.program = sample()

    # 使用默认空策略调用场景检查，并允许测试覆盖程序及检查选项。
    def inspect(self, program=None, **options):
        return inspect_program(self.root, self.program if program is None else program,
                               policy=options.pop('policy', {}), **options)

    # 写入完整有效的生产计划，使关系测试实际经过保存的简报。
    def write_wall_art_brief(self):
        design = {'id': 'design', 'name': 'Gallery', 'summary': 'A framed print on the gallery wall',
                  'hard_requirements': ['Keep the print on the room wall'],
                  'physical_assumptions': ['The wall and frame are static'],
                  'object_roles': [{'object_id': 'wall_art', 'role': 'framed print'}],
                  'relations': [{'source': 'wall_art', 'type': 'mounted_to', 'target': 'building_shell',
                                 'description': 'The wall carries the frame'}]}
        plan = self.root / 'plan.json'
        plan.write_text(json.dumps({'schema': 'benchforge.assessment/v1',
                                    'scenes': {'designs': [design]}}), encoding='utf-8')
        self.program['provenance'] = {'sources': [{'kind': 'scene_plan', 'path': str(plan),
                                                  'scene_design_id': 'design'}]}

    # 挂载声明缺字段时一次性给出字段、类型和值域及动态对象严格性，避免关系错误掩盖根因。
    def test_malformed_mount_reports_fields_without_null_relation_duplicate(self):
        self.write_wall_art_brief()
        self.program['objects'] = [
            object_record('building_shell', kind='box', size=[4, 4, 3], dynamic=False),
            object_record('wall_art', kind='box', size=[1, .1, 1], support='ground',
                          dynamic=False, metadata={'spatial_attachment': {'target': 'building_shell'}}),
        ]
        report = self.inspect(policy={'enforce_realism': True})
        mount = next(row for row in report['issues'] if row['code'] == 'static_mount')
        self.assertEqual(mount['evidence']['mount_declaration_present'], True)
        self.assertIn('method', mount['evidence']['missing_fields'])
        self.assertIn('axis', mount['evidence']['missing_fields'])
        self.assertIn('dynamic_requirement', mount['evidence'])
        self.assertIn('Required fields:', mount['message'])
        self.assertIn('dynamic must be exactly false', mount['message'])
        self.assertEqual(report['scene_brief']['id'], 'design')
        self.assertFalse(any(row['code'] == 'scene_brief_relation_mismatch'
                             for row in report['issues']))

    # 挂载关系未声明时保留关系问题，但明确这是缺失声明而非读取失败。
    def test_missing_mount_relation_identifies_absent_declaration(self):
        self.write_wall_art_brief()
        self.program['objects'] = [
            object_record('building_shell', kind='box', size=[4, .2, 3], dynamic=False,
                          tags=['architectural_structure']),
            object_record('wall_art', kind='mug', size=[.0924, .1272, .091], dynamic=False),
        ]
        report = self.inspect(policy={'enforce_realism': True})
        issue = next(row for row in report['issues']
                     if row['code'] == 'scene_brief_relation_mismatch')
        self.assertFalse(issue['evidence']['mount_declaration_present'])
        self.assertEqual(issue['evidence']['mismatch_kind'], 'missing_mount_declaration')
        self.assertEqual(issue['path'], '/objects/1/metadata/spatial_attachment')
        self.assertIn('item_id=mounting', issue['message'])
        self.assertFalse(issue['blocking'])
        self.assertEqual(issue['severity'], 'warning')
        self.assertEqual(issue['evidence']['review_required'],
                         'captured_view_and_native_relation_check')
        self.assertTrue(report['passed'])
        self.assertEqual(report['status'], 'needs_verification')

    def test_mounting_contract_is_discoverable(self):
        response = catalog_response(None, {'section': 'contracts', 'item_id': 'mounting'})
        contract = response['items'][0]['value']
        self.assertEqual(contract['field_path'], 'objects[].metadata.spatial_attachment')
        self.assertEqual(contract['required_fields'], ['target', 'method', 'axis', 'object_face', 'target_face'])

    def test_window_mount_and_cabinet_support_are_gated_by_capture(self):
        mounted = {'target': 'wall_east', 'method': 'static_mount', 'axis': 1,
                   'object_face': 'min', 'target_face': 'max'}
        self.program['objects'] = [
            object_record('wall_east', label='East wall', size=[2, .2, 2]),
            object_record('wall_west', label='West wall', size=[2, .2, 2], xy=[0, 2]),
            object_record('window_frame', label='Fixed window frame', size=[1.2, .1, .8],
                          xy=[0, .15], base_z=.8, metadata={'spatial_attachment': mounted}),
            object_record('floor_plinth', label='Floor plinth', size=[.9, 1, .2], xy=[3, 0]),
            object_record('cabinet_east', label='Cabinet', size=[.5, .5, .5],
                          xy=[3, 0], support='floor_plinth'),
        ]
        relations = [
            {'source': 'window_frame', 'type': 'mounted_to', 'target': 'wall_east',
             'description': 'The window is fixed to the east wall'},
            {'source': 'cabinet_east', 'type': 'supported_by', 'target': 'floor_plinth',
             'description': 'The cabinet rests on the plinth'},
        ]
        design = {'id': 'design', 'name': 'Window and cabinet', 'summary': 'An east window and cabinet',
                  'hard_requirements': ['Include a mounted window and a supported cabinet'],
                  'physical_assumptions': ['All objects are static'],
                  'object_roles': [{'object_id': 'window_frame', 'role': 'window frame'},
                                   {'object_id': 'cabinet_east', 'role': 'storage cabinet'}],
                  'relations': relations}
        exception = {'scene_design_id': 'design', 'scene_id': 'test', 'object_ids': ['cabinet_east'],
                     'purpose': 'Validate the specified contacts', 'reason': 'Isolate geometry',
                     'simplifications': 'Cabinet envelope', 'retained_constraints': 'Cabinet support',
                     'excluded_claims': 'Furniture realism'}
        plan = self.root / 'plan.json'
        plan.write_text(json.dumps({'schema': 'benchforge.assessment/v1',
                                    'scenes': {'designs': [design], 'geometry_exceptions': [exception]}}),
                        encoding='utf-8')
        self.program['provenance'] = {'sources': [{'kind': 'scene_plan', 'path': str(plan),
                                                  'scene_design_id': 'design'}]}
        (self.root / 'scene_policy.json').write_text('{"enabled":true,"enforce_realism":true}', encoding='utf-8')
        capture = self.root / 'capture'
        capture.mkdir()
        (capture / 'view_0.png').write_bytes(b'fixture-view')
        observation = {'view': 'view_0.png', 'observation': 'Window and cabinet are visible.'}
        review = {
            'acceptable': True,
            'visual_realism': {'status': 'matches_declared_geometry'},
            'life_review': {'checks': [{'check': name, 'passed': True, 'evidence': [observation]}
                                       for name in VISUAL_CHECKS]},
            'brief_alignment': {'passed': True, 'hard_requirements_preserved': True,
                                'observations': [observation], 'unresolved': [],
                                'requirement_checks': [{'index': 0, 'passed': True, **observation}],
                                'relation_checks': []},
        }
        report = {'status': 'captured', 'renderable': True, 'token': 'fixture-capture'}
        bounds = {
            'wall_east': ([-1, -.1, 0], [1, .1, 2]),
            'wall_west': ([-1, 1.9, 0], [1, 2.1, 2]),
            'window_frame': ([-.6, .1, .8], [.6, .2, 1.6]),
            'floor_plinth': ([2.55, -.5, 0], [3.45, .5, .2]),
            'cabinet_east': ([2.75, -.25, .2], [3.25, .25, .7]),
        }
        for mismatch in (None, 'window_frame', 'cabinet_east'):
            with self.subTest(mismatch=mismatch), patch(
                    'capabilities.scene_authoring.validation.policy.environment_value', return_value=None):
                program = copy.deepcopy(self.program)
                measured = bounds.copy()
                if mismatch == 'window_frame':
                    program['objects'][2].update(xy=[0, 2.15], metadata={
                        'spatial_attachment': {**mounted, 'target': 'wall_west'}})
                    measured[mismatch] = ([-.6, 2.1, .8], [.6, 2.2, 1.6])
                elif mismatch == 'cabinet_east':
                    program['objects'][4].update(xy=[4, 0], support='ground')
                    measured[mismatch] = ([3.75, -.25, 0], [4.25, .25, .5])
                evidence = {'origin': 'isaac_native', 'token': report['token'],
                            'physics': {'stable': True, 'steps': 60, 'dt': 1 / 60},
                            'frames': [{'frame_id': 'view_0'}],
                            'entities': {key: {'world_aabb': {'min': lo, 'max': hi},
                                               'collision': {'paths': ['/' + key]}}
                                         for key, (lo, hi) in measured.items()}}
                admission = admit_program(self.root, program, execution_mode='production')
                warnings = [row for row in admission['issues'] if row['code'] == 'scene_brief_relation_mismatch']
                self.assertEqual(len(warnings), int(mismatch is not None))
                self.assertTrue(all(not row['blocking'] for row in warnings))
                receipt = capture_acceptance(self.root, program, report, evidence, review, capture,
                                             execution_mode='production')
                self.assertEqual(receipt['passed'], mismatch is None, receipt['issues'])
                self.assertEqual([row['source'] for row in receipt['native_relation_checks'] if not row['passed']],
                                 [mismatch] if mismatch else [])
                self.assertEqual(bool(receipt['physical_issues']), mismatch == 'window_frame')
                if mismatch == 'cabinet_east':
                    check = next(row for row in receipt['native_relation_checks'] if row['source'] == mismatch)
                    self.assertIsNone(check['passed'])
                    self.assertTrue(check['visual_check_required'])
                    self.assertIn('Scene brief needs complete captured evidence for relation_checks', receipt['issues'])

    # 目标已声明但未通过对象准入时，明确说明几何检查未执行。
    def test_mount_target_without_geometry_is_explicitly_not_evaluated(self):
        self.write_wall_art_brief()
        self.program['objects'] = [
            object_record('building_shell', kind='box', size=[0, 4, 3], dynamic=False),
            object_record('wall_art', kind='box', dynamic=False,
                metadata={'spatial_attachment': {'target': 'building_shell', 'method': 'static_mount',
                                                 'axis': 1, 'object_face': 'min', 'target_face': 'max'}}),
        ]
        report = self.inspect(policy={'enforce_realism': True})
        self.assertFalse(any(row['code'] == 'scene_brief_relation_mismatch'
                             for row in report['issues']))
        skipped = next(row for row in report['skipped_checks'] if row['check'] == 'static_mount_geometry')
        self.assertIn('is declared but unavailable to geometry checks', skipped['reason'])
        self.assertEqual(skipped['evidence']['target_id'], 'building_shell')
        self.assertTrue(skipped['evidence']['target_declared'])
        self.assertFalse(skipped['evidence']['target_admitted'])
        self.assertEqual(skipped['status'], 'not_evaluated')
        self.assertTrue(any(row['blocking'] and row['path'] == '/objects/0/size'
                            for row in report['issues']))
        self.assertEqual(report['status'], 'blocked')

    # 非字符串目标也应返回结构化字段错误。
    def test_mount_target_with_wrong_json_type_returns_field_diagnostics(self):
        for target in (['building_shell'], {'id': 'building_shell'}):
            with self.subTest(target=target):
                self.program['objects'] = [object_record(
                    'wall_art', metadata={'spatial_attachment': {'target': target}})]
                report = self.inspect()
                evidence = next(row['evidence'] for row in report['issues'] if row['code'] == 'static_mount')
                self.assertIn('target', evidence['invalid_fields'])
                self.assertEqual(evidence['declared_target'], target)
                self.assertFalse(evidence['geometry_target_available'])

    # 几何不可评估时仍可用声明 ID 对账简报中的目标。
    def test_unavailable_mount_geometry_preserves_wrong_declared_target(self):
        self.write_wall_art_brief()
        self.program['objects'] = [
            object_record('building_shell'), object_record('other_wall', size=[0, 4, 3]),
            object_record('wall_art', metadata={'spatial_attachment': {
                'target': 'other_wall', 'method': 'static_mount', 'axis': 1,
                'object_face': 'min', 'target_face': 'max'}})]
        report = self.inspect(policy={'require_scene_brief': True})
        mismatch = next(row['evidence'] for row in report['issues']
                        if row['code'] == 'scene_brief_relation_mismatch')
        self.assertEqual(mismatch['expected_target'], 'building_shell')
        self.assertEqual(mismatch['declared_target'], 'other_wall')
        self.assertEqual(mismatch['mismatch_kind'], 'target_mismatch')

    # 简报目标不符但场景程序可渲染时，预检保留警告并放行首轮图像检查。
    def test_relation_mismatch_is_nonblocking_for_first_capture(self):
        self.write_wall_art_brief()
        self.program['objects'] = [
            object_record('building_shell', kind='box', size=[4, .2, 3], dynamic=False,
                          tags=['architectural_structure']),
            object_record('wall_art', kind='mug', size=[.0924, .1272, .091], dynamic=False),
        ]
        report = self.inspect(policy={'require_scene_brief': True})
        issue = next(row for row in report['issues']
                     if row['code'] == 'scene_brief_relation_mismatch')
        self.assertEqual(issue['severity'], 'warning')
        self.assertFalse(issue['blocking'])
        self.assertTrue(report['passed'])
        self.assertEqual(report['status'], 'needs_verification')

    # 验证字段、对象、相机和其他区块的独立错误全部被收集。
    def test_independent_fields_objects_cameras_and_sections_are_collected(self):
        self.program['objects'] = [
            object_record('first', size=[0, 1, 1], xy=[1], mass_kg=-1,
                          appearance={'uv_scale_m': 2}),
            object_record('second', color=[2, 0, 0], support='missing'),
        ]
        self.program['cameras'] = [
            {'position': [0, 0, 0], 'target': [0, 0, 0], 'focal_length_mm': -1},
            {'position': [1], 'target': [0, 0, 0]},
        ]
        self.program['materials'] = [{'id': 'm1'}, {'id': 'm2', 'name': '', 'roughness': 3}]
        self.program['lights'] = [{'id': 'l1', 'kind': 'point'}, {'id': 'l2', 'kind': 'unknown'}]
        original = copy.deepcopy(self.program)
        report = self.inspect()
        paths = {row['path'] for row in report['issues']}
        self.assertTrue({'/objects/0/size', '/objects/0/xy', '/objects/0/mass_kg',
                         '/objects/0/appearance', '/objects/1/color', '/objects/1/support',
                         '/cameras/0', '/cameras/0/focal_length_mm', '/cameras/1/position',
                         '/materials/0', '/materials/1', '/lights/0', '/lights/1'} <= paths)
        self.assertFalse(report['passed'])
        self.assertEqual(self.program, original)
        self.assertEqual(report['runtime_checks']['status'], 'not_evaluated')
        with self.assertRaises(ProgramValidationError) as caught:
            validate_program(self.program)
        self.assertEqual(caught.exception.issues, program_contract_issues(self.program))

    # 验证外观格式错误不遮蔽独立的尺寸与放置问题。
    def test_invalid_appearance_does_not_hide_independent_scale_and_placement(self):
        self.program['objects'] = [object_record('cup', kind='mug', size=[9, 9, 9],
                                                base_z=1, appearance={'opacity': .5})]
        report = self.inspect(policy={'enforce_realism': True})
        codes = {row['code'] for row in report['issues']}
        self.assertTrue({'invalid_scene_contract', 'native_asset_scale', 'ground_offset',
                         'scene_brief'} <= codes)
        self.assertEqual(len(report['geometry_preview']['objects']), 1)

    # 验证设计简报中独立要求与空间关系问题均进入诊断。
    def test_brief_diagnostics_include_independent_requirements_and_relations(self):
        plan = self.root / 'plan.json'
        plan.write_text(json.dumps({'schema':'benchforge.assessment/v1', 'scenes':{'designs':[{
            'id':'design', 'name':'', 'summary':'', 'hard_requirements':[],
            'physical_assumptions':[], 'object_roles':[{'object_id':'absent','role':'decoration'}],
            'relations':[{'source':'unknown_object','type':'supported_by','target':'absent',
                          'description':'support'}],
        }]}}), encoding='utf-8')
        self.program['provenance'] = {'sources':[{'kind':'scene_plan','path':str(plan),
                                                'scene_design_id':'design'}]}
        report = self.inspect(policy={'enforce_realism':True})
        brief = next(row for row in report['issues'] if row['code'] == 'scene_brief')
        for field in ('/name', '/summary', '/hard_requirements', '/physical_assumptions',
                      '/object_roles/0', '/relations/0'):
            self.assertIn(field, brief['message'])
        self.assertFalse(any(row['code'] == 'trellis_asset_required' for row in report['issues']))

    def test_authored_geometry_needs_only_declared_representation_and_physics(self):
        self.write_wall_art_brief()
        self.program['objects'] = [
            object_record('building_shell', size=[4, .2, 3], label='Wall'),
            object_record('wall_art', xy=[1, 1], size=[.5, .3, .2], label='Marker'),
        ]
        policy = {'enforce_realism': True}
        report = self.inspect(policy=policy)
        self.assertTrue(report['passed'], report['issues'])
        self.assertFalse(any(row['code'] == 'trellis_asset_required' for row in report['issues']))

        self.program['objects'][1].update(dynamic=True, tags=['articulation_required'])
        blocked = self.inspect(policy=policy)
        self.assertFalse(blocked['passed'])
        self.assertTrue({'specialist_asset_required', 'physical_material_missing'} <=
                        {row['code'] for row in blocked['issues'] if row['blocking']})

    def test_spatial_reference_mismatch_identifies_saved_design_and_current_ids(self):
        plan = self.root / 'plan.json'
        design = {
            'id': 'desk_layout', 'name': 'Desk layout', 'summary': 'A desk and chair arrangement.',
            'hard_requirements': ['The chair faces the desk.'],
            'physical_assumptions': ['The desk and chair remain static.'],
            'object_roles': [{'object_id': 'unknown_object', 'role': 'desk'}],
            'relations': [{'source': 'unknown_object', 'type': 'supported_by',
                           'target': 'ground', 'description': 'The desk rests on the floor.'}],
            'spatial_reference': {
                'schema': 'benchforge.review-reference/v1', 'world_up': [0, 0, 1],
                'frames': [{'id': 'desk_user', 'kind': 'user', 'origin_object_id': 'bench_e2',
                            'observer_object_id': 'stool_e2', 'facing_target_object_id': 'bench_e2',
                            'front': 'toward_observer'}],
            },
        }
        plan.write_text(json.dumps({'schema': 'benchforge.assessment/v1',
                                    'scenes': {'designs': [design]}}), encoding='utf-8')
        self.program['provenance'] = {'sources': [{'kind': 'scene_plan', 'path': str(plan),
                                                   'scene_design_id': 'desk_layout'}]}

        report = self.inspect(policy={'enforce_realism': True})

        issue = next(row for row in report['issues'] if row['code'] == 'scene_brief')
        for expected in ('plan.json', 'desk_layout', 'desk_user', "origin_object_id='bench_e2'",
                         "observer_object_id='stool_e2'", 'current program object IDs: unknown_object'):
            self.assertIn(expected, issue['message'])

    # 验证尺寸无效时仍继续报告资源缺失。
    def test_missing_assets_are_reported_even_when_dimensions_are_invalid(self):
        self.program['objects'] = [object_record('a', kind='mesh', asset_id='missing_a', size=[0, 1, 1]),
                                   object_record('b', kind='mesh', asset_id='missing_b')]
        report = self.inspect()
        missing = [row for row in report['issues'] if row['code'] == 'source_geometry_unavailable']
        self.assertEqual({row['path'] for row in missing}, {'/objects/0/asset_id', '/objects/1/asset_id'})
        self.assertTrue(any(row['path'] == '/objects/0/size' for row in report['issues']))

    # 验证支撑图报告所有问题连通分量，不猜造无法确定的高度。
    def test_support_graph_reports_all_components_and_does_not_invent_heights(self):
        self.program['objects'] = [
            object_record('a', support='b'), object_record('b', support='a'),
            object_record('c', support='d'), object_record('d', support='c'),
            object_record('orphan', support='gone'), object_record('child', support='orphan'),
            object_record('independent'),
        ]
        report = self.inspect()
        cycles = [row for row in report['issues'] if row['code'] == 'cyclic_support']
        self.assertEqual({frozenset(row['object_ids']) for row in cycles},
                         {frozenset(['a', 'b']), frozenset(['c', 'd'])})
        self.assertTrue(any(row['code'] == 'missing_support' for row in report['issues']))
        self.assertEqual([row['object_id'] for row in report['geometry_preview']['objects']], ['independent'])
        self.assertTrue(any(row['path'] == '/objects/5' for row in report['skipped_checks']))

    def test_preflight_accepts_transitive_support_and_leaves_component_identity_to_capture(self):
        self.program['objects'] = [object_record('leg'), object_record('seat', support='leg'),
                                   object_record('shelf'), object_record('box', support='shelf')]
        brief = {'object_roles': [], 'relations': [
            {'type': 'supported_by', 'source': 'seat', 'target': 'ground'},
            {'type': 'supported_by', 'source': 'box', 'target': 'leg'},
        ]}
        with patch('capabilities.scene_authoring.validation.preflight.scene_brief', return_value=brief):
            report = self.inspect(policy={'enabled': True})
        mismatches = [row for row in report['issues'] if row['code'] == 'scene_brief_relation_mismatch']
        self.assertEqual(len(mismatches), 1)
        self.assertEqual(mismatches[0]['evidence']['relation_index'], 1)
        self.assertEqual(mismatches[0]['evidence']['support_path'], ['box', 'shelf', 'ground'])
        self.assertFalse(mismatches[0]['blocking'])

    # 验证待生成对象不因尚无网格误失败，也不妨碍检查其他几何。
    def test_pending_generated_object_does_not_hide_other_geometry_or_fail_by_itself(self):
        self.program['objects'] = [
            object_record('pending', kind='generated', generation={'prompt': 'a ceramic cat'}, xy=[5, 5]),
            object_record('left'), object_record('right', xy=[.2, 0]),
        ]
        report = self.inspect()
        self.assertTrue(report['passed'])
        self.assertTrue(any(row['code'] == 'asset_not_realized' for row in report['issues']))
        overlap = next(row for row in report['issues'] if row['code'] == 'initial_bounds_overlap')
        self.assertEqual(set(overlap['object_ids']), {'left', 'right'})
        self.assertFalse(overlap['blocking'])
        self.assertIn('unresolved', overlap['message'])
        self.assertFalse(self.inspect(allow_generated=False)['passed'])

    # 验证房间相机建议仅作为提示，不被升级为生产拒绝。
    def test_room_camera_advice_is_not_a_production_rejection(self):
        self.program['rooms'] = [{'id': 'room', 'name': 'room',
                                  'bounds': {'center': [0, 0, 1.5], 'size': [4, 4, 3]}}]
        self.program['cameras'] = [{'position': [0, 0, 7], 'target': [0, 0, 1]}]
        report = self.inspect()
        camera = next(row for row in report['issues'] if row['code'] == 'camera_above_declared_ceiling')
        self.assertEqual(camera['basis'], 'declared_room_bounds')
        self.assertEqual(camera['severity'], 'warning')
        self.assertFalse(camera['blocking'])
        self.assertTrue(report['passed'])
        self.assertEqual(report['runtime_checks']['status'], 'not_evaluated')

    # 验证相关支撑问题被分组展示，诊断不改写用户场景。
    def test_related_support_problems_are_grouped_without_rewriting_the_scene(self):
        self.program['objects'] = [
            object_record('shelf'), object_record('tray', support='shelf', base_z=.3),
            object_record('cup', support='tray', base_z=.2),
        ]
        original = copy.deepcopy(self.program)
        report = self.inspect()
        gaps = [row for row in report['issues'] if row['code'] == 'support_contact_unverified']
        self.assertEqual({row['object_ids'][0] for row in gaps}, {'tray', 'cup'})
        group = next(row for row in report['repair_groups'] if 'tray' in row['object_ids'])
        self.assertEqual(set(group['object_ids']), {'shelf', 'tray', 'cup'})
        self.assertTrue({row['id'] for row in gaps} <= set(group['issue_ids']))
        self.assertTrue(report['passed'])
        self.assertEqual(self.program, original)

    # 验证同一网格在一次预检中只读取一次，避免重复 I/O。
    def test_reused_mesh_is_read_once_in_a_preflight(self):
        directory = self.root / 'generated_assets' / 'asset_fixture'
        directory.mkdir(parents=True)
        mesh_path = directory / 'mesh.json'
        mesh_path.write_text(json.dumps({'coordinate_frame': 'z_up', 'vertices': [[x, y, z] for x in (-1, 1)
                                                    for y in (-1, 1) for z in (-1, 1)]}), encoding='utf-8')
        self.program['objects'] = [object_record('a', kind='mesh', asset_id='asset_fixture'),
                                   object_record('b', kind='mesh', asset_id='asset_fixture', xy=[3, 0])]
        reads = []
        original = Path.read_text
        # 记录网格读取路径并转发原读取方法，供缓存命中次数断言。
        def read(path, *args, **kwargs):
            reads.append(path)
            return original(path, *args, **kwargs)
        with patch.object(Path, 'read_text', read), \
                patch('capabilities.scene_authoring.geometry.preview.mesh_fit_preview', wraps=mesh_fit_preview) as fit:
            report = self.inspect()
        self.assertEqual(reads.count(mesh_path), 1)
        fit.assert_called_once()
        self.assertTrue(report['passed'])
        self.assertEqual(report['geometry_preview']['mesh_assets_read'], 1)

    def test_production_rejects_separated_static_support_before_capture(self):
        floor = object_record('floor', label='Floor slab', size=[6, 5, .1])
        ceiling = object_record('ceiling', label='Ceiling', size=[6, 5, .1], support='floor', base_z=2.8)
        mug = object_record('mug_fixture', kind='mug', size=[.0924, .1272, .091])
        self.program['objects'] = [floor, ceiling, mug]
        report = self.inspect(policy={'enabled': True})
        errors = [row for row in report['issues'] if row['blocking']]
        self.assertEqual([(row['code'], row['object_ids']) for row in errors],
                         [('static_support_gap', ['ceiling', 'floor'])])
        self.assertAlmostEqual(errors[0]['evidence']['gap_m'], 2.8)

        wall = object_record('wall', label='Wall', size=[6, .1, 2.8], support='floor')
        self.program['objects'].append(wall)
        ceiling.update(support='wall', base_z=0)
        self.assertTrue(self.inspect(policy={'enabled': True})['passed'])

    def test_geometry_warnings_are_linked_to_task_relations_without_blocking(self):
        self.program['objects'] = [
            object_record('ledge'), object_record('tray', support='ledge', xy=[.3, 0]),
            object_record('chair', xy=[2, 0]), object_record('bin_obj', xy=[2.2, 0]),
            object_record('background', xy=[3, 0]),
        ]
        brief = {'object_roles': [], 'relations': [
            {'type': 'supported_by', 'source': 'tray', 'target': 'ground'},
            {'type': 'clearance', 'source': 'chair', 'target': 'bin_obj'},
            {'type': 'near', 'source': 'bin_obj', 'target': 'chair', 'requires_measurement': True},
        ]}
        with patch('capabilities.scene_authoring.validation.preflight.scene_brief', return_value=brief):
            report = self.inspect(policy={'enabled': True})
        issues = {row['id']: row for row in report['issues']}
        reviews = report['task_geometry_reviews']
        self.assertEqual([group['relation_index'] for group in reviews], [0, 1, 2])
        self.assertEqual([[issues[i]['code'] for i in group['issue_ids']] for group in reviews],
                         [['support_contact_unverified'], ['initial_bounds_overlap'], ['initial_bounds_overlap']])
        self.assertTrue(any('background' in row.get('object_ids', []) for row in issues.values()))
        self.assertTrue(report['passed'], report['issues'])

    # 验证超预算支撑图明确报错，不触发递归几何爆炸或错误成功。
    def test_over_budget_graph_is_reported_without_recursive_geometry_or_false_success(self):
        self.program['objects'] = [object_record(f'obj_{i}', support=f'obj_{i-1}' if i else 'ground')
                                   for i in range(1100)]
        report = self.inspect()
        self.assertFalse(report['passed'])
        self.assertEqual(report['geometry_preview']['objects'], [])
        self.assertTrue(any(row['path'] == '/objects' for row in report['skipped_checks']))

    # 验证畸形记录不导致预检崩溃或掩盖其他问题。
    def test_malformed_records_do_not_hide_other_diagnostics_or_crash(self):
        for field, values in {
            'objects': [None, [None, []], [{'id': []}], [{'id': 'broken'}],
                        [{'id': [], 'kind': 'mesh', 'asset_id': 'missing'}]],
            'cameras': [None, [None, []], [{'position': [], 'target': None}]],
            'rooms': [None, [None, {'id': 'room'}]],
            'materials': [None, [False, {'id': []}]],
            'provenance': [None, [], {'sources': [None]}],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    candidate = copy.deepcopy(self.program)
                    candidate[field] = value
                    candidate['lights'] = [{'id': 'broken', 'kind': 'point'}]
                    report = self.inspect(candidate)
                    self.assertFalse(report['passed'])
                    self.assertTrue(any(row['path'] == '/lights/0' for row in report['issues']))
                    json.dumps(report, allow_nan=False)

    # 验证大量错误时仍保留完整报告，并以摘要绑定原输入。
    def test_full_report_survives_large_error_lists_and_is_bound_to_its_input(self):
        self.program['objects'] = [object_record(f'object_{i}', support=f'missing_{i}') for i in range(80)]
        workspace = self.root / 'operator_workspaces' / 'test'
        workspace.mkdir(parents=True)
        source = workspace / 'scene.json'
        source.write_text(json.dumps(self.program), encoding='utf-8')
        with self.assertRaises(ScenePreflightError) as caught:
            read_program_submission(self.root, str(source), execution_mode='exploration')
        report = caught.exception.report
        self.assertEqual(report['summary']['errors'], 80)
        self.assertIn('missing_79', str(caught.exception))
        self.assertGreater(len(str(caught.exception)), 1800)
        self.assertEqual(json.loads(Path(report['report_path']).read_text(encoding='utf-8')), report)
        self.assertEqual(json.loads(Path(report['program_snapshot_path']).read_text(encoding='utf-8')), self.program)
        self.assertFalse(list(self.root.glob('sf_*')))
        self.program['objects'] = [object_record('fixed')]
        source.write_text(json.dumps(self.program), encoding='utf-8')
        fixed = inspect_program_submission(self.root, str(source), execution_mode='exploration')
        self.assertTrue(fixed['passed'])
        self.assertEqual(report['summary']['errors'], 80)
        self.assertNotEqual(report['report_path'], fixed['report_path'])

    # 验证并行预检各自保留独立回执，不相互覆盖。
    def test_parallel_preflights_keep_independent_receipts(self):
        workspace = self.root / 'operator_workspaces' / 'test'
        workspace.mkdir(parents=True)
        source = workspace / 'scene.json'
        source.write_text(json.dumps(self.program), encoding='utf-8')
        with ThreadPoolExecutor(max_workers=4) as pool:
            reports = list(pool.map(lambda _: inspect_program_submission(
                self.root, str(source), execution_mode='exploration'), range(8)))
        self.assertEqual(len({row['report_path'] for row in reports}), 8)
        for row in reports:
            self.assertTrue(row['passed'])
            self.assertEqual(json.loads(Path(row['report_path']).read_text(encoding='utf-8')), row)

    # 验证旧预览摘要明确标出省略警告数量及完整报告位置。
    def test_legacy_preview_summary_declares_omitted_warnings_and_the_exact_report(self):
        preview = {'authority':'static geometry only', 'objects':[],
                   'warnings':[{'object_id':f'obj_{i}','code':'preview_incomplete'} for i in range(40)]}
        result = compact_preview(preview, full_report='/task/model_calls/scene_plan_1/geometry_preview.json')
        self.assertEqual(result['total_warnings'], 40)
        self.assertEqual(result['truncated_warnings'], 8)
        self.assertTrue(result['read_full_report_before_repair'])
        self.assertEqual(result['full_report'], '/task/model_calls/scene_plan_1/geometry_preview.json')

    # 验证已保存任务的完整诊断和规划预览可以再次读取。
    def test_saved_task_diagnostics_and_planner_preview_are_retrievable(self):
        root = self.root
        class Store:
            # 返回固定失败任务及修订身份，供历史诊断读取测试。
            def snapshot(self, _):
                return {'tasks':[{'id':'sf_test.scene0','state':'FAILED_FINAL','unit':{'revision':0}}]}
        store = Store()
        store.root = root
        directory = root / 'sf_test.scene0' / 'revision_0'
        files = ['scene_preflight.json', 'scene_admission.json', 'scene_acceptance.json',
                 'model_calls/scene_plan_0/geometry_preview.json']
        for name in files:
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"diagnostic":"full report"}', encoding='utf-8')
        listed = evidence(store, 'sf_test.scene0')['revisions'][0]['files']
        self.assertTrue(set(files) <= {row['file'] for row in listed})
        for name in files:
            self.assertEqual(evidence(store, 'sf_test.scene0', file=name)['data'],
                             {'diagnostic':'full report'})


if __name__ == '__main__':
    unittest.main()
