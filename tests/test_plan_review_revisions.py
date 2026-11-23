"""Regression: scene repairs must not restart a released S1 window."""
from capabilities.scene_authoring.contracts.scene_design import validate_scene_brief
from pipelines.benchmark.design.plan_review import revision_review
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import tempfile
import unittest

from tests.database_fixtures import use_postgres
from database.engine import engine_for
from sqlalchemy import text
from unittest.mock import patch

from pipelines.benchmark.design.planner import plan
from pipelines.benchmark.design.plan_versions import current
from storage.artifacts import read, write
from storage.execution_lock import exclusive_run
from pipelines.reviews.service import review
from pipelines.benchmark.execution.documents import RunDocuments
from service_access.access_scope import TrustedAccess, access_context
from storage.access_identity import register_session_workspace


class PlanReviewRevisionTests(unittest.TestCase):
    # 建立独立会话、可控时钟及评估计划夹具，供修订与评审身份测试。
    def setUp(self):
        self.database_url = use_postgres(self)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root, self.session, self.index = Path(temporary.name), 'revision-session', 0
        self.clock = 1000
        for name in ('validate_assessment', 'assessment_with_current_schema'):
            validator = patch('pipelines.benchmark.design.planner.' + name, side_effect=lambda value, *args: value)
            validator.start(); self.addCleanup(validator.stop)
        validator = patch('capabilities.benchmark_design.assessment.validate_assessment', side_effect=lambda value, *args: value)
        validator.start(); self.addCleanup(validator.stop)
        clock = patch('storage.stage_reviews.time.time', side_effect=lambda: self.clock)
        clock.start(); self.addCleanup(clock.stop)
        self.args = {'objective': 'An indoor benchmark',
            'selections': [{'template_ids': ['C43-T01'], 'count': 10, 'reason': 'Distance and time'}],
            'inputs': {'evidence': 'tasks/room/evidence.jsonl'},
            'assessment': {'dataset': {'target_items': 10},
                'annotations': {'require_robot_locomotion': True},
                'scoring': {'rule': 'Use the native trajectory'},
                'scenes': {'target_scene_count': 1, 'designs': [{'id': 'room', 'artifact': 'tasks/room/views',
                    'hard_requirements': ['Use the official robot', 'Record ten native trajectories'],
                    'object_roles': [{'object_id': 'sofa', 'role': 'Furniture obstacle'},
                                     {'object_id': 'dog_go2', 'role': 'Robot', 'representation': 'articulated_robot'},
                                     {'object_id': 'floor', 'role': 'Support plane'}],
                    'relations': [{'source': 'sofa', 'type': 'supported_by', 'target': 'floor', 'description': 'On the floor'}]}],
                    'geometry_exceptions': [{'scene_design_id': 'room', 'scene_id': 'room',
                        'object_ids': ['sofa', 'sofa_arm'], 'reason': 'Simplified furniture only'}]}}}

    # 复制计划参数并分配独立操作目录，以指定或默认会话保存计划。
    def save(self, args=None, session=None):
        self.index += 1
        return plan({**deepcopy(self.args if args is None else args),
                     '_dsh_context': {'session_id': session or self.session}},
                    self.root / 'operations' / str(self.index))

    # 验证等价计划重复保存保留原评审时间窗，并展示最新产物。
    def test_repeated_saves_keep_the_original_window_and_publish_the_latest_artifact(self):
        first = self.save()
        self.clock += 30
        for _ in range(3):
            saved = self.save()
            self.assertEqual(saved['stage_review']['review_id'], first['stage_review']['review_id'])
            self.assertEqual(saved['stage_review']['deadline'], first['stage_review']['deadline'])
            self.assertEqual(saved['stage_review']['state'], 'waiting_user')
            self.assertEqual(saved['review_revision']['kind'], 'execution_repair')
        self.assertNotEqual(saved['plan'], first['plan'])
        self.assertEqual(review(self.root, self.session)['artifact'], saved['plan'])
        self.assertEqual(current(self.root, self.session)['plan'], saved['plan'])
        self.assertTrue(Path(first['plan']).is_file())

    # 验证用户继续后进行场景修复，不重启已完成的评审轮次。
    def test_continue_then_scene_repairs_do_not_restart_checked_rounds(self):
        first = self.save()
        review(self.root, self.session, 'continue')
        changed = deepcopy(self.args)
        design = changed['assessment']['scenes']['designs'][0]
        design['object_roles'][0]['object_id'] = 'sofa_seat'
        design['relations'][0]['source'] = 'sofa_seat'
        changed['assessment']['scenes']['geometry_exceptions'][0]['object_ids'] = ['sofa_seat']
        changed['inputs']['scene_sources'] = [{'task_id': 'accepted-scene', 'revision': 1}]
        changed['inputs']['evidence'] = 'tasks/room/repaired-evidence.jsonl'
        design['artifact'] = 'tasks/room/latest-views'
        self.clock += 35
        for _ in range(3):
            saved = self.save(changed)
            self.assertEqual(saved['stage_review']['review_id'], first['stage_review']['review_id'])
            self.assertEqual(saved['stage_review']['deadline'], first['stage_review']['deadline'])
            self.assertEqual(saved['stage_review']['state'], 'continued')
            self.assertTrue(saved['stage_review']['can_continue'])
            self.assertNotIn('action=wait', saved['next'])
            self.assertEqual(saved['review_revision']['changed_fields'], [])

    # 验证评审等待已完成的计划重新保存后仍保持放行。
    def test_elapsed_window_remains_released_on_resave(self):
        first = self.save()
        for _ in range(3):
            self.clock += 60
            review(self.root, self.session)
        saved = self.save()
        self.assertEqual(saved['stage_review']['review_id'], first['stage_review']['review_id'])
        self.assertEqual(saved['stage_review']['state'], 'elapsed')
        self.assertTrue(saved['stage_review']['can_continue'])
        self.assertNotIn('action=wait', saved['next'])

    # 验证描述修复保留等待进度，而实质契约变化开启新评审。
    def test_soft_description_repair_retains_waits_but_hard_contract_does_not(self):
        first = self.save()
        self.clock += 60
        second = review(self.root, self.session)
        changed = deepcopy(self.args)
        changed['assessment']['scenes']['designs'][0].update(
            summary='Corrected furniture description based on the saved view',
            revision_history=[{'revision': 2, 'changes': ['Clarified the description']}])
        repaired = self.save(changed)
        self.assertEqual(repaired['stage_review']['review_id'], first['stage_review']['review_id'])
        self.assertEqual(repaired['stage_review']['deadline'], second['deadline'])
        self.assertEqual(repaired['stage_review']['waits_completed'], 1)
        changed['assessment']['scenes']['designs'][0]['hard_requirements'] = ['A proxy is sufficient']
        with self.assertRaisesRegex(ValueError, 'preserve original hard requirements'):
            self.save(changed)

    def test_frame_rename_keeps_deadline_changed_observer_restarts(self):
        args = deepcopy(self.args)
        design = args['assessment']['scenes']['designs'][0]
        frame = {'id': 'viewpoint', 'kind': 'user', 'origin_object_id': 'floor',
                 'observer_object_id': 'sofa', 'facing_target_object_id': 'dog_go2', 'front': 'toward_observer'}
        design['spatial_reference'] = {'schema': 'benchforge.review-reference/v1', 'world_up': [0, 0, 1], 'frames': [frame]}
        first = self.save(args)['stage_review']
        self.clock += 30
        design['object_roles'][0]['object_id'] = 'seat'
        design['relations'][0]['source'] = frame['observer_object_id'] = 'seat'
        args['assessment']['scenes']['geometry_exceptions'][0]['object_ids'] = ['seat', 'sofa_arm']
        second = self.save(args)['stage_review']
        self.assertEqual((second['review_id'], second['deadline']), (first['review_id'], first['deadline']))
        frame['observer_object_id'] = 'dog_go2'
        frame['facing_target_object_id'] = 'seat'
        third = self.save(args)['stage_review']
        self.assertNotEqual(third['review_id'], first['review_id'])
        self.assertGreater(third['deadline'], second['deadline'])

    # 验证数量、评分等硬契约变化生成新评审，并列出变化字段。
    def test_contract_changes_require_a_new_review_and_report_changed_fields(self):
        cases = {
            'quantity': lambda args: args['assessment']['dataset'].update(target_items=5),
            'scoring': lambda args: args['assessment']['scoring'].update(rule='Approximate freely'),
            'exception_scope': lambda args: args['assessment']['scenes']['geometry_exceptions'][0].update(reason='All assets may be boxes'),
            'relationship': lambda args: args['assessment']['scenes']['designs'][0]['relations'][0].update(type='near'),
        }
        for name, change in cases.items():
            with self.subTest(name=name):
                session = 'material-' + name
                first = self.save(session=session)
                review(self.root, session, 'continue')
                changed = deepcopy(self.args); change(changed)
                saved = self.save(changed, session)
                self.assertNotEqual(saved['stage_review']['review_id'], first['stage_review']['review_id'])
                self.assertEqual(saved['stage_review']['state'], 'waiting_user')
                self.assertEqual(saved['review_revision']['kind'], 'contract_change')
                self.assertTrue(saved['review_revision']['changed_fields'])

    # 验证重用同一请求文件位置不能掩盖其内容中的要求变化。
    def test_same_request_path_does_not_hide_a_requirement_change(self):
        request = self.root / 'tasks/room/request.json'
        write(request, self.args)
        first = self.save({'request_path': str(request)})
        self.assertEqual(self.save({'request_path': str(request)})['stage_review']['review_id'], first['stage_review']['review_id'])
        changed = deepcopy(self.args); changed['assessment']['annotations']['require_robot_locomotion'] = False
        write(request, changed)
        with self.assertRaisesRegex(ValueError, 'preserve require_robot_locomotion'):
            self.save({'request_path': str(request)})

    # 验证执行修复不能把必需机器人改成几何例外以绕过要求。
    def test_repair_cannot_turn_a_required_robot_into_a_geometry_exception(self):
        self.save()
        changed = deepcopy(self.args)
        changed['assessment']['scenes']['geometry_exceptions'][0]['object_ids'].append('dog_go2')
        with self.assertRaisesRegex(ValueError, 'cannot replace required native assets'):
            self.save(changed)

    # 验证正常布局修复和显式新任务仍可保存，不被契约保护误拦截。
    def test_ordinary_layout_repairs_and_new_tasks_remain_available(self):
        self.save()
        changed = deepcopy(self.args)
        changed['assessment']['scenes']['designs'][0]['summary'] = 'Moved the sofa away from the doorway'
        self.assertTrue(self.save(changed)['review_revision']['review_reused'])
        changed['new_task'] = True
        changed['assessment']['scenes']['designs'][0]['hard_requirements'] = ['A new geometry experiment']
        self.assertEqual(self.save(changed)['review_revision']['kind'], 'new_task')

    # 验证只有显式新任务建立新谱系，移动请求文件仍复用原审核。
    def test_explicit_new_task_is_independent_of_request_location(self):
        first = self.save()
        separate = self.save({**self.args, 'new_task': True})
        self.assertNotEqual(separate['stage_review']['review_id'], first['stage_review']['review_id'])
        one, two = self.root / 'tasks/one/request.json', self.root / 'tasks/two/request.json'
        write(one, self.args); write(two, self.args)
        first = self.save({'request_path': str(one)})
        separate = self.save({'request_path': str(two)})
        self.assertEqual(separate['review_revision']['kind'], 'execution_repair')
        self.assertEqual(separate['stage_review']['review_id'], first['stage_review']['review_id'])

    def test_unfinished_execution_rejects_replacement_before_saving_or_validation(self):
        """真实数据库内未完成的 S2 阻止隐式换任务，预检与保存返回同一个续跑入口。"""
        scope = register_session_workspace(engine_for(self.database_url), host_id='host-test',
            session_id=self.session, owner_id='owner-test', root_path=str(self.root))
        first = self.save()
        review(self.root, self.session, 'continue')
        run = Path(first['plan']).parent / 'pipeline_run'
        with access_context(TrustedAccess(scope, str(self.root))):
            docs = RunDocuments(run)
            docs.save('plan.json', read(first['plan']))
            docs.save('state.json', {'state': 'needs_evidence', 'run_path': str(run),
                'steps': {'accepted_scenes': {'state': 'needs_evidence'}}})
        for new_task in (False, True):
            for validate_only in (False, True):
                changed = deepcopy(self.args)
                changed.update(new_task=new_task, validate_only=validate_only)
                changed['assessment']['dataset']['target_items'] = 5
                result = self.save(changed)
                self.assertEqual(result['state'], 'needs_execution_repair')
                self.assertEqual(result['stage'], 'S2')
                self.assertEqual(result['resume']['arguments'], {'plan': first['plan'], 'run': str(run)})
                self.assertFalse(result['review_revision']['applied'])
                self.assertFalse((self.root / 'operations' / str(self.index) / 'plan.json').exists())
                self.assertEqual(current(self.root, self.session)['plan'], first['plan'])
                self.assertEqual(review(self.root, self.session)['review_id'], first['stage_review']['review_id'])
        # 普通修复继续可用，且文件副本伪造完成不能解除生产保护。
        write(run / 'state.json', {'state': 'completed'})
        self.assertTrue(self.save()['review_revision']['review_reused'])
        self.assertEqual(self.save(changed)['state'], 'needs_execution_repair')
        with access_context(TrustedAccess(scope, str(self.root))):
            docs.save('state.json', {'state': 'completed', 'run_path': str(run),
                                   'steps': {'accepted_scenes': {'state': 'completed'}}})
        changed['validate_only'] = False
        self.assertEqual(self.save(changed)['state'], 'planned')

    def test_unfinished_execution_still_allows_verified_workbench_changes(self):
        """用户经工作台修改需求仍可保存草稿，不能把修复边界变成无法修改需求。"""
        first = self.save()
        changed = deepcopy(self.args)
        changed['assessment']['dataset']['target_items'] = 5
        changed['_plan_edit'] = {'base_plan': first['plan'], 'changes': ['dataset'], 'application_required': True}
        with patch('pipelines.benchmark.design.continuity.latest_production_execution') as lookup:
            self.assertEqual(self.save(changed)['state'], 'planned')
            lookup.assert_not_called()

    # 验证改名或内联提交不能绕过原任务硬要求保护。
    def test_relocated_request_cannot_drop_requirements(self):
        one, two = self.root / 'original.json', self.root / 'revision.json'
        write(one, self.args)
        self.save({'request_path': str(one)})
        changed = deepcopy(self.args)
        changed['assessment']['annotations']['require_robot_locomotion'] = False
        write(two, changed)
        with self.assertRaisesRegex(ValueError, 'preserve require_robot_locomotion'):
            self.save({'request_path': str(two)})

    # 验证文件改为内联时继承省略的执行约束和原模板分组，不再意外重开 S1。
    def test_file_to_inline_revision_keeps_production_inputs(self):
        args = deepcopy(self.args)
        args['inputs'].update(execution_mode='production', quantity_contract='minimum',
                             scene_selection={'allow_new_scenes': True, 'selected_scenes': []})
        request = self.root / 'request.json'
        write(request, args)
        first = self.save({'request_path': str(request)})
        review(self.root, self.session, 'continue')
        saved = self.save({'objective': args['objective'], 'assessment': args['assessment'],
                           'template_ids': ['C43-T01'], 'target_items': 10})
        self.assertEqual(saved['stage_review']['review_id'], first['stage_review']['review_id'])
        self.assertTrue(saved['stage_review']['can_continue'])
        self.assertEqual(saved['review_revision']['changed_fields'], [])
        document = read(saved['plan'])
        for key in ('execution_mode', 'quantity_contract', 'scene_selection'):
            self.assertEqual(document['inputs'][key], args['inputs'][key])
            self.assertIn('inputs.' + key, saved['inherited_fields'])

    # 验证修复计划保留用户暂停和待处理指令状态。
    def test_repairs_preserve_pause_and_pending_instruction(self):
        for action in ('pause', 'instruction'):
            with self.subTest(action=action):
                session = 'preserve-' + action
                first = self.save(session=session)
                paused = review(self.root, session, action, instruction='Keep the official robot')
                self.clock += 600
                saved = self.save(session=session)
                self.assertEqual(saved['stage_review']['review_id'], first['stage_review']['review_id'])
                self.assertEqual(saved['stage_review']['state'], paused['state'])
                self.assertEqual(saved['stage_review']['instruction'], paused['instruction'])
                self.assertFalse(saved['stage_review']['can_continue'])
                with self.assertRaisesRegex(ValueError, 'Resolve the paused review'):
                    self.save({**self.args, 'new_task': True}, session)
                self.assertEqual(current(self.root, session)['plan'], saved['plan'])

    # 验证单独声称应用用户编辑不能为新要求继承旧审批。
    def test_unverified_applies_edit_does_not_grant_approval_for_new_requirements(self):
        first = self.save(); review(self.root, self.session, 'continue')
        changed = deepcopy(self.args)
        changed.update(applies_edit=first['plan'])
        changed['assessment']['dataset']['target_items'] = 1
        saved = self.save(changed)
        self.assertEqual(saved['review_revision']['kind'], 'contract_change')
        self.assertFalse(saved['stage_review']['can_continue'])

    # 验证已核实的工作台编辑保持原评审及用户暂停状态。
    def test_verified_workbench_edit_keeps_its_original_review_and_pause(self):
        first = self.save()
        review(self.root, self.session, 'pause')
        changed = deepcopy(self.args)
        changed['assessment']['dataset']['target_items'] = 5
        changed['assessment']['scenes']['designs'][0]['hard_requirements'] = ['User explicitly requested a different robot experiment']
        changed['_plan_edit'] = {'base_plan': first['plan'], 'changes': ['dataset'], 'application_required': True}
        draft = self.save(changed)
        self.assertIsNone(draft['stage_review'])
        document = read(draft['plan'])
        args = {key: document[key] for key in ('objective', 'pipeline_id', 'assessment', 'inputs', 'steps', 'selections')}
        args['inputs']['evidence'] = 'tasks/room/user-requested-experiment/evidence.jsonl'
        saved = self.save({**args, 'applies_edit': draft['plan']})
        self.assertEqual(saved['review_revision']['kind'], 'user_edit')
        self.assertEqual(saved['stage_review']['review_id'], first['stage_review']['review_id'])
        self.assertEqual(saved['stage_review']['state'], 'paused')
        self.assertFalse(current(self.root, self.session)['pending'])
        self.assertEqual(read(saved['plan'])['assessment']['dataset']['target_items'], 5)
        self.assertEqual(read(saved['plan'])['assessment']['scenes']['designs'][0]['hard_requirements'],
                         changed['assessment']['scenes']['designs'][0]['hard_requirements'])

    # 验证同会话并发保存原子化，冲突只产生明确忙碌错误。
    def test_repeated_plan_saves_are_atomic_per_conversation(self):
        self.save()
        args = {**self.args, '_dsh_context': {'session_id': self.session}}
        # 并发保存同一会话计划，将预期锁竞争转成可检查的空结果。
        def save(index):
            try:
                return plan(deepcopy(args), self.root / 'operations' / ('parallel-' + str(index)))
            except ValueError as error:
                if 'Another plan revision' not in str(error):
                    raise
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(save, range(10)))
        self.assertTrue(any(results))
        self.assertEqual(review(self.root, self.session)['artifact'], current(self.root, self.session)['plan'])
        with engine_for(self.database_url).connect() as db:
            self.assertEqual(db.execute(text('SELECT COUNT(*) FROM reviews WHERE session=:session'), {'session':self.session}).scalar_one(), 1)

    # 验证计划锁只阻止同会话，不影响另一会话保存。
    def test_plan_save_lock_does_not_block_another_conversation(self):
        lock = self.root / 'plan-locks' / sha256(self.session.encode()).hexdigest()
        with exclusive_run(lock):
            with self.assertRaisesRegex(ValueError, 'Another plan revision'):
                self.save()
            self.assertEqual(self.save(session='another-session')['stage_review']['state'], 'waiting_user')


class RelationContractReviewTests(unittest.TestCase):
    def setUp(self):
        self.relations = [
            {'source': 'cabinet_east', 'type': 'supported_by', 'target': 'ground',
             'description': 'The cabinet stands on the floor.'},
            {'source': 'cabinet_east', 'type': 'supported_by', 'target': 'desk_main',
             'description': 'The floor-standing cabinet is to the right of the desk.',
             'reference_frame': 'desk_frame', 'expected_direction': ['right']},
            {'source': 'window_main', 'type': 'mounted_to', 'target': 'building_shell',
             'description': 'The window is attached to the building shell.'},
        ]
        self.objects = {'cabinet_east', 'desk_main', 'window_main', 'building_shell'}
        self.design = {
            'id': 'study', 'name': 'Study', 'summary': 'Study with a desk, cabinet and window.',
            'hard_requirements': ['A study with real furniture'],
            'physical_assumptions': ['The cabinet is static.'],
            'object_roles': [{'object_id': key, 'role': key} for key in sorted(self.objects)],
            'relations': self.relations,
            'spatial_reference': {'schema': 'benchforge.review-reference/v1', 'world_up': [0, 0, 1],
                'frames': [{'id': 'desk_frame', 'kind': 'world', 'origin_object_id': 'desk_main',
                            'front_world': [0, 1, 0], 'right_world': [1, 0, 0]}]},
        }
        self.previous = {
            'production_contract': 'benchforge.production-stages/v1',
            'stage_review_key': 'original-review', 'user_markdown': 'Keep the original furniture.',
            'authoring_sources': {'request_path': 'request.json'},
            'assessment': {'scenes': {'designs': [self.design]}},
        }

    def check_revision(self, indices):
        result = deepcopy(self.previous)
        design = result['assessment']['scenes']['designs'][0]
        for index in indices:
            design['relations'][index]['type'] = 'near'
        validate_scene_brief(self.design, self.objects)
        validate_scene_brief(design, self.objects)
        key, detail = revision_review(result, 'revised-plan.json', self.previous, 'original-plan.json')
        self.assertEqual(key, str(Path('revised-plan.json').resolve()))
        self.assertEqual(detail['base_plan'], 'original-plan.json')
        self.assertEqual(detail['kind'], 'contract_change')
        self.assertFalse(detail['review_reused'])
        self.assertEqual(detail['changed_fields'],
                         [f'assessment.scenes.designs.0.relations.{index}.type' for index in indices])
        return result

    def test_cabinet_and_window_changes_are_listed_in_the_fresh_review(self):
        for indices in ((1,), (2,), (1, 2)):
            with self.subTest(indices=indices):
                self.check_revision(indices)
        self.assertEqual([row['type'] for row in self.relations],
                         ['supported_by', 'supported_by', 'mounted_to'])

    def test_multiple_actual_supports_are_not_equivalent_to_near(self):
        self.design['hard_requirements'].append('The cabinet legs and the desk rail both carry the cabinet.')
        self.relations[1]['description'] = 'The desk rail carries part of the cabinet load on its right side.'
        revised = self.check_revision((1,))
        revised['assessment']['scenes']['designs'][0]['hard_requirements'] = []
        with self.assertRaisesRegex(ValueError, 'preserve original hard requirements'):
            revision_review(revised, 'revised-plan.json', self.previous, 'original-plan.json')


if __name__ == '__main__':
    unittest.main()
