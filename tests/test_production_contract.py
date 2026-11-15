import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from capabilities.scene_library.acceptance import accepted_scenes
from capabilities.dataset_authoring.quality import validate_choice
from pipelines.benchmark.design.production_steps import complete_production_steps, validate_production_steps
from pipelines.benchmark.execution.review_steps import review_scope
from pipelines.benchmark.execution.runner import run
from service_access.production_receipts import SceneReceipts
from capabilities.scene_library.episodes import native_episodes
from capabilities.dataset_authoring.trajectory_evidence import validate_native_episode
from pipelines.benchmark.execution.production_stages import validate_planned_scenes, scene_acceptance
from pipelines.benchmark.design.planner import plan


class ProductionContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def recipe(self):
        return [{'id': 'questions', 'uses': 'templates.produce', 'arguments': {'evidence': '$inputs.evidence'}},
                {'id': 'package', 'uses': 'data.package', 'arguments': {'dataset': '$questions.dataset'}}]

    def test_default_plan_has_all_stage_dependencies(self):
        steps = complete_production_steps(self.recipe())
        validate_production_steps(steps)
        scope = review_scope({'steps': steps}, self.root, self.root / 'plan.json')
        self.assertEqual([len(scope['members'][key]) for key in ('S2', 'S3', 'S4', 'S5')], [1, 1, 1, 1])
        self.assertEqual(steps[2]['arguments']['evidence'], '$collection.evidence')
        self.assertEqual(steps[1]['uses'], 'collection.validate')

    def test_s2_counts_scenes_not_revisions_trajectories_or_items(self):
        sources = [{'task_id': 'sf_scene.scene0', 'revision': index} for index in range(100)]
        accepted = [{**row, 'scene_group_id': 'same-scene'} for row in sources]
        environment = {'inputs': {}, '_assessment': {'scenes': {
            'target_scene_count': 2, 'selected_scenes': sources, 'allow_new_scenes': False}}}
        with patch.object(SceneReceipts, 'require', return_value=accepted):
            result = scene_acceptance({'scene_sources': sources}, self.root, environment, None)
        self.assertEqual(result['state'], 'needs_evidence')
        self.assertEqual(result['accepted'], 1)
        self.assertEqual(result['required'], 2)

    def test_semantic_stage_wins_over_misleading_review_labels(self):
        steps = complete_production_steps(self.recipe())
        for step in steps:
            step['review_stage'] = 'S2'
        scope = review_scope({'steps': steps}, self.root, self.root / 'plan.json')
        self.assertEqual(scope['members']['S3'][0]['uses'], 'collection.validate')
        self.assertEqual(scope['members']['S4'][0]['uses'], 'templates.produce')

    def test_review_labels_cannot_hide_missing_dependencies(self):
        steps = self.recipe()
        steps[0]['review_stage'] = 'S2'
        with self.assertRaisesRegex(ValueError, 'S4'):
            validate_production_steps(steps)

    def test_old_saved_plan_is_blocked_before_question_generation(self):
        file = self.root / 'plan.json'
        file.write_text(json.dumps({'steps': self.recipe(), 'assessment': {'execution': ['S1', 'S2', 'S3', 'S4', 'S5']}, 'inputs': {}}))
        with patch('pipelines.benchmark.execution.runner.load_policy', return_value={'execution_mode': 'production'}), \
             patch('pipelines.benchmark.execution.runner.produce') as producer:
            result = run({'plan': str(file), 'run': str(self.root / 'run'), '_workspace': str(self.root)}, self.root)
        self.assertEqual(result['state'], 'needs_plan_revision')
        producer.assert_not_called()

    def test_robot_trajectory_objective_cannot_disable_native_episode_requirements(self):
        directory = self.root / 'operations' / 'plan-test'
        args = {'objective': '生产100条机器狗在室内探索的轨迹数据',
                'selections': [{'template_ids': ['C43-T01'], 'count': 1, 'reason': 'controlled test'}],
                'assessment': {'annotations': {'require_native_episode': False, 'require_robot_locomotion': False}}}
        with patch('pipelines.benchmark.design.planner.validate_assessment', side_effect=lambda value, *unused: value), \
             patch('pipelines.benchmark.design.planner.assessment_with_current_schema', side_effect=lambda value: value):
            result = plan(args, directory)
        saved = json.loads(Path(result['plan']).read_text(encoding='utf-8'))
        self.assertTrue(saved['assessment']['annotations']['require_native_episode'])
        self.assertTrue(saved['assessment']['annotations']['require_robot_locomotion'])
        validate_production_steps(saved['steps'])

    def test_public_capture_file_changes_invalidate_service_acceptance(self):
        store, folder, task = self.service()
        file = folder / 'capture/view_0.png'
        file.write_bytes(b'accepted-image')
        task['result']['scene_review']['production_acceptance']['evidence_versions']['capture/view_0.png'] = [file.stat().st_size, file.stat().st_mtime_ns]
        accepted_scenes(store, [{'task_id': 'sf_example.scene0', 'revision': 0}])
        file.write_bytes(b'replaced-image-with-different-length')
        with self.assertRaisesRegex(ValueError, 'artifact changed'):
            accepted_scenes(store, [{'task_id': 'sf_example.scene0', 'revision': 0}])

    def service(self, passed=True):
        folder = self.root / 'sf_example.scene0/revision_0'
        versions = {}
        for name in ('program.json', 'capture/report.json', 'capture/evidence.json'):
            file = folder / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text('{}')
            versions[name] = [file.stat().st_size, file.stat().st_mtime_ns]
        receipt = {'passed': passed, 'enforced': True, 'evidence_versions': versions, 'scene_id': 'room'}
        task = {'id': 'sf_example.scene0', 'state': 'SUCCEEDED', 'unit': {'revision': 0, 'intent': {'split': 'train'}},
                'result': {'scene_review': {'passed': passed, 'production_acceptance': receipt}}}
        class Store:
            root = self.root
            def snapshot(self, _run):
                return {'tasks': [task]}
        return Store(), folder, task

    def test_handwritten_acceptance_cannot_override_service_failure(self):
        store, folder, _task = self.service(passed=False)
        (folder / 'scene_acceptance.json').write_text('{"passed":true,"enforced":true}')
        with self.assertRaisesRegex(ValueError, 'service acceptance'):
            accepted_scenes(store, [{'task_id': 'sf_example.scene0', 'revision': 0}])

    def test_changed_evidence_cannot_be_reapproved_with_a_local_version_edit(self):
        store, folder, task = self.service()
        changed = folder / 'capture/evidence.json'
        changed.write_text('{"changed":true}')
        fake = copy.deepcopy(task['result']['scene_review']['production_acceptance'])
        fake['evidence_versions']['capture/evidence.json'] = [changed.stat().st_size, changed.stat().st_mtime_ns]
        (folder / 'scene_acceptance.json').write_text(json.dumps(fake))
        with self.assertRaisesRegex(ValueError, 'evidence changed'):
            accepted_scenes(store, [{'task_id': 'sf_example.scene0', 'revision': 0}])

    def test_accepted_scene_identity_comes_from_service(self):
        store, _folder, _task = self.service()
        result = accepted_scenes(store, [{'task_id': 'sf_example.scene0', 'revision': 0}])
        self.assertEqual(result['scenes'][0]['scene_group_id'], 'scene-v1:sf_example.scene0')
        self.assertEqual(result['scenes'][0]['split'], 'train')

    def test_repeated_items_share_one_batched_acceptance_query(self):
        store, _folder, _task = self.service()
        calls = []
        def call(route, value, _config):
            calls.append(route)
            return accepted_scenes(store, value['scene_sources'])
        receipts = SceneReceipts('client.json', call)
        sources = [{'task_id': 'sf_example.scene0', 'revision': 0}] * 100
        receipts.require(sources)
        for source in sources:
            receipts.for_record({'provenance': source})
        self.assertEqual(calls, ['/production/acceptance'])

    def test_choice_ids_must_be_visible_to_answerer(self):
        with self.assertRaisesRegex(ValueError, 'public option IDs'):
            validate_choice({'output_kind': 'choice'}, {'choice_id': 'A'}, {'options': ['左转', '右转']})
        validate_choice({'output_kind': 'choice'}, {'choice_id': 'A'}, {'options': ['A: 左转', 'B: 右转']})

    def test_accepted_but_unselected_scene_cannot_replace_saved_selection(self):
        receipt = {'task_id': 'sf_unselected.scene0', 'revision': 0, 'scene_design': {'id': 'other'}}
        assessment = {'scenes': {'allow_new_scenes': False,
                                'selected_scenes': [{'task_id': 'sf_selected.scene0', 'revision': 0}]}}
        with self.assertRaisesRegex(ValueError, 'selection'):
            validate_planned_scenes([receipt], assessment)

    def test_new_capture_must_match_the_complete_scene_design(self):
        design = {'id': 'room', 'name': '室内房间', 'hard_requirements': ['保留走廊']}
        assessment = {'scenes': {'allow_new_scenes': True, 'designs': [design]}}
        source = {'task_id': 'sf_example.scene0', 'revision': 0, 'scene_design': design}
        validate_planned_scenes([source], assessment)
        source['scene_design'] = {**design, 'hard_requirements': []}
        with self.assertRaisesRegex(ValueError, 'saved scene design'):
            validate_planned_scenes([source], assessment)

    def test_episode_claim_cannot_use_static_scene_or_camera_motion_as_robot_control(self):
        with self.assertRaisesRegex(ValueError, 'intermediate states'):
            validate_native_episode({'provenance': {'native_episode_id': 'walk'}}, {}, required=True)
        receipt = {'native_episodes': [{'id': 'camera', 'action': 'record_trajectory', 'succeeded': True, 'samples': 10}]}
        with self.assertRaisesRegex(ValueError, 'Camera recording'):
            validate_native_episode({'provenance': {'native_episode_id': 'camera'}}, receipt, required=True, interaction=True)

    def test_robot_episode_requires_a_named_native_actor_and_increasing_states(self):
        record = {'provenance': {'native_episode_id': 'walk', 'robot_actor_id': 'dog-1'}}
        episode = {'id': 'walk', 'action': 'robot_locomotion', 'robot_id': 'dog-1', 'succeeded': True,
                   'samples': 3, 'first_step': 0, 'last_step': 2,
                   'actor_states': [{'actor_id': 'dog-1', 'step': step,
                                     'position': [step / 10, 0, 1], 'orientation_wxyz': [1, 0, 0, 0]}
                                    for step in range(3)]}
        receipt = {'native_episodes': [episode]}
        self.assertIs(validate_native_episode(record, receipt, required=True, robot_locomotion=True), episode)
        episode['action'] = 'apply_force'
        with self.assertRaisesRegex(ValueError, 'robot_locomotion'):
            validate_native_episode(record, receipt, required=True, robot_locomotion=True)
        episode['action'] = 'robot_locomotion'
        episode['actor_states'][1]['step'] = 2
        with self.assertRaisesRegex(ValueError, 'steps must increase'):
            validate_native_episode(record, receipt, required=True, robot_locomotion=True)

    def test_trajectory_endpoints_and_rewritten_files_are_not_accepted(self):
        _store, folder, task = self.service()
        trajectory = folder / 'capture/trajectory.json'
        evidence = folder / 'capture/evidence.json'
        evidence.write_text(json.dumps({'interaction': {'action_results': [
            {'id': 'push', 'action': 'apply_force', 'sample_count': 3, 'success': True, 'trajectory_file': 'trajectory.json'}]}}))
        sample = lambda step: {'step': step, 'timestamp_sim': step * .01}
        value = {'id': 'push', 'action': 'apply_force', 'dt': .01, 'trajectory': [sample(0), sample(1), sample(2)]}
        trajectory.write_text(json.dumps(value))
        receipt = task['result']['scene_review']['production_acceptance']
        receipt['evidence_versions']['capture/trajectory.json'] = [trajectory.stat().st_size, trajectory.stat().st_mtime_ns]
        episodes = native_episodes(folder, receipt)
        self.assertEqual(episodes[0]['samples'], 3)
        trajectory.write_text(json.dumps({**value, 'trajectory': [sample(0), sample(2)]}))
        with self.assertRaisesRegex(ValueError, 'trajectory changed'):
            native_episodes(folder, receipt)
        receipt['evidence_versions']['capture/trajectory.json'] = [trajectory.stat().st_size, trajectory.stat().st_mtime_ns]
        with self.assertRaisesRegex(ValueError, 'intermediate samples'):
            native_episodes(folder, receipt)


if __name__ == '__main__':
    unittest.main()
