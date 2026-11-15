"""Regression: scene repairs must not restart a released S1 window."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from pipelines.benchmark.design.planner import plan
from pipelines.benchmark.design.plan_versions import current
from storage.artifacts import read, write
from storage.execution_lock import exclusive_run
from storage.stage_reviews import review


class PlanReviewRevisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root, self.session, self.index = Path(temporary.name), 'revision-session', 0
        self.clock = 1000
        for name in ('validate_assessment', 'assessment_with_current_schema'):
            validator = patch('pipelines.benchmark.design.planner.' + name, side_effect=lambda value, *args: value)
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
                                     {'object_id': 'floor', 'role': 'Support plane'}],
                    'relations': [{'source': 'sofa', 'type': 'supported_by', 'target': 'floor', 'description': 'On the floor'}]}],
                    'geometry_exceptions': [{'scene_design_id': 'room', 'scene_id': 'room',
                        'object_ids': ['sofa', 'sofa_arm'], 'reason': 'Simplified furniture only'}]}}}

    def save(self, args=None, session=None):
        self.index += 1
        return plan({**deepcopy(self.args if args is None else args),
                     '_dsh_context': {'session_id': session or self.session}},
                    self.root / 'operations' / str(self.index))

    def test_repeated_saves_keep_the_original_window_and_publish_the_latest_artifact(self):
        first = self.save()
        self.clock += 70
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

    def test_continue_then_scene_repairs_do_not_add_another_five_minutes(self):
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

    def test_elapsed_window_remains_released_on_resave(self):
        first = self.save()
        self.clock += 301
        saved = self.save()
        self.assertEqual(saved['stage_review']['review_id'], first['stage_review']['review_id'])
        self.assertEqual(saved['stage_review']['state'], 'elapsed')
        self.assertTrue(saved['stage_review']['can_continue'])
        self.assertNotIn('action=wait', saved['next'])

    def test_contract_changes_require_a_new_review_and_report_changed_fields(self):
        cases = {
            'quantity': lambda args: args['assessment']['dataset'].update(target_items=5),
            'requirements': lambda args: args['assessment']['scenes']['designs'][0].update(hard_requirements=['A box is enough']),
            'native_collection': lambda args: args['assessment']['annotations'].update(require_robot_locomotion=False),
            'scoring': lambda args: args['assessment']['scoring'].update(rule='Approximate freely'),
            'robot_exception': lambda args: args['assessment']['scenes']['geometry_exceptions'][0]['object_ids'].append('dog_go2'),
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

    def test_same_request_path_does_not_hide_a_requirement_change(self):
        request = self.root / 'tasks/room/request.json'
        write(request, self.args)
        first = self.save({'request_path': str(request)})
        self.assertEqual(self.save({'request_path': str(request)})['stage_review']['review_id'], first['stage_review']['review_id'])
        changed = deepcopy(self.args); changed['assessment']['annotations']['require_robot_locomotion'] = False
        write(request, changed)
        self.assertFalse(self.save({'request_path': str(request)})['review_revision']['review_reused'])

    def test_new_request_and_explicit_new_task_do_not_reuse_review(self):
        first = self.save()
        separate = self.save({**self.args, 'new_task': True})
        self.assertNotEqual(separate['stage_review']['review_id'], first['stage_review']['review_id'])
        one, two = self.root / 'tasks/one/request.json', self.root / 'tasks/two/request.json'
        write(one, self.args); write(two, self.args)
        first = self.save({'request_path': str(one)})
        separate = self.save({'request_path': str(two)})
        self.assertEqual(separate['review_revision']['kind'], 'new_task')
        self.assertNotEqual(separate['stage_review']['review_id'], first['stage_review']['review_id'])

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

    def test_unverified_applies_edit_does_not_grant_approval_for_new_requirements(self):
        first = self.save(); review(self.root, self.session, 'continue')
        changed = deepcopy(self.args)
        changed.update(applies_edit=first['plan'])
        changed['assessment']['dataset']['target_items'] = 1
        saved = self.save(changed)
        self.assertEqual(saved['review_revision']['kind'], 'contract_change')
        self.assertFalse(saved['stage_review']['can_continue'])

    def test_verified_workbench_edit_keeps_its_original_review_and_pause(self):
        first = self.save()
        review(self.root, self.session, 'pause')
        changed = deepcopy(self.args)
        changed['assessment']['dataset']['target_items'] = 5
        changed['_plan_edit'] = {'base_plan': first['plan'], 'changes': ['dataset'], 'application_required': True}
        draft = self.save(changed)
        self.assertIsNone(draft['stage_review'])
        document = read(draft['plan'])
        args = {key: document[key] for key in ('objective', 'pipeline_id', 'assessment', 'inputs', 'steps', 'selections')}
        saved = self.save({**args, 'applies_edit': draft['plan']})
        self.assertEqual(saved['review_revision']['kind'], 'user_edit')
        self.assertEqual(saved['stage_review']['review_id'], first['stage_review']['review_id'])
        self.assertEqual(saved['stage_review']['state'], 'paused')
        self.assertFalse(current(self.root, self.session)['pending'])
        self.assertEqual(read(saved['plan'])['assessment']['dataset']['target_items'], 5)

    def test_repeated_plan_saves_are_atomic_per_conversation(self):
        self.save()
        args = {**self.args, '_dsh_context': {'session_id': self.session}}
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
        with sqlite3.connect(self.root / 'stage-reviews.sqlite') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM reviews WHERE session=?', (self.session,)).fetchone()[0], 1)

    def test_plan_save_lock_does_not_block_another_conversation(self):
        lock = self.root / 'plan-locks' / sha256(self.session.encode()).hexdigest()
        with exclusive_run(lock):
            with self.assertRaisesRegex(ValueError, 'Another plan revision'):
                self.save()
            self.assertEqual(self.save(session='another-session')['stage_review']['state'], 'waiting_user')


if __name__ == '__main__':
    unittest.main()
