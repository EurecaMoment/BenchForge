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
        self.assertTrue(any(row['code'] == 'recognizable_asset_required' for row in report['issues']))

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
