"""Exercise the real stage runner and packager with a controlled service receipt."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from capabilities.dataset_authoring.adapters import generate
from capabilities.dataset_authoring.question_writing import builtin_spec
from capabilities.task_catalog.benchmark import all_templates
from pipelines.benchmark.design.production_steps import complete_production_steps
from pipelines.benchmark.execution.runner import run
from storage.artifacts import write, jsonl
from pipelines.benchmark.execution.review_service import review
from pipelines.benchmark.design.plan_versions import activate


class PipelineStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.session = 'session-stage-test'
        template = next(row for row in all_templates() if row['id'] == 'C43-T01')
        sample = generate(template['id'], 17, self.root / 'media')
        scene_capture = self.root / 'sf_test.scene0/revision_0/capture'
        scene_capture.mkdir(parents=True)
        self.native_source = scene_capture / 'evidence.json'
        write(self.native_source, sample['source'])
        source = self.root / 'derived-answer.json'
        admission = dict(template_id=template['id'], protocol_id=template['protocol_id'],
                         information_sufficient=True, oracle_executed=True, media_consistent=True, no_answer_leakage=True)
        write(source, {'answer': sample['oracle'], 'admission': admission})
        self.derivation = {
            'schema': 'benchforge.native-derivation/v1', 'oracle': 'builtin-template/v1',
            'inputs': {key: {'path': str(self.native_source), 'selector': '/' + key}
                       for key in ('distance', 'speed')},
        }
        self.record = {'template_id': template['id'], 'parameters': sample['parameters'],
                      'media': [], 'provided_information': sample['provided_information'],
                      'provenance': {'kind': 'simulation', 'path': str(source), 'task_id': 'sf_test.scene0', 'revision': 0,
                                     'derivation': self.derivation},
                      'answer_selector': '/answer', 'admission_selector': '/admission',
                      'oracle_version': 'test-controlled-receipt', 'reference_frame': sample['reference_frame'],
                      'scene_group_id': 'author-chosen-name', 'split': 'train',
                      'question_spec': builtin_spec(template, sample)}
        self.evidence = self.root / 'evidence.jsonl'
        jsonl(self.evidence, [self.record])
        recipe = json.loads((Path(__file__).parents[1] / 'src/pipelines/benchmark/recipes/data.capability.json').read_text(encoding='utf-8'))
        self.plan = self.root / 'plan.json'
        write(self.plan, {'schema': 'benchforge.pipeline/v1', 'production_contract': 'benchforge.production-stages/v1',
                          'inputs': {'selections': [{'template_ids': [template['id']], 'count': 1}], 'seed': 17, 'split': 'train', 'scene_sources': []},
                          'assessment': {'scenes': {'target_scene_count': 1, 'allow_new_scenes': False,
                                                  'selected_scenes': [{'task_id': 'sf_test.scene0', 'revision': 0}]}},
                          'steps': complete_production_steps(recipe['steps'])})
        activate(self.root, self.session, str(self.plan))
        # This runner fixture supplies controlled stage receipts, not a full
        # model-authored assessment. Review validation has its own real tests.
        assessment = patch('capabilities.benchmark_design.assessment.validate_assessment', side_effect=lambda value, *args: value)
        assessment.start(); self.addCleanup(assessment.stop)
        self.calls = []
        self.policy = patch('pipelines.benchmark.execution.runner.load_policy', side_effect=self.load_policy)
        self.policy.start(); self.addCleanup(self.policy.stop)
        dataset_policy = patch('capabilities.dataset_authoring.production.load_policy', return_value={'execution_mode': 'production', 'enabled': True, 'enforce_realism': True})
        dataset_policy.start(); self.addCleanup(dataset_policy.stop)
        def initialize_receipts(instance, config_path, call=None):
            self.receipts(instance, config_path, self.native_source)
        receipt = patch('service_access.production_receipts.SceneReceipts.__init__', initialize_receipts)
        receipt.start(); self.addCleanup(receipt.stop)

    def load_policy(self, _workspace, config):
        self.calls.append(config)
        return {'execution_mode': 'production'}

    @staticmethod
    def receipts(instance, config_path, native_source):
        instance.config_path = config_path
        instance.receipts = {}
        def accept(_route, args, _config):
            scenes = []
            for source in args['scene_sources']:
                directory = native_source.parents[1]
                scenes.append({**source, 'state': 'accepted', 'authority': 'production-service-task-record',
                               'split': 'train', 'scene_group_id': 'scene-v1:' + source['task_id'],
                               'source_directory': str(directory),
                               'artifacts': [str(native_source.resolve())]})
            return {'scenes': scenes}
        instance.call = accept

    def advance(self, bindings=None):
        return run({'plan': str(self.plan), 'run': str(self.root / 'run'), '_workspace': str(self.root),
                    '_dsh_context': {'session_id': self.session}, 'bindings': bindings or {}}, self.root)

    def continue_review(self):
        return review(self.root, self.session, 'continue')

    def test_full_five_stage_review_retains_bindings_and_packages_real_files(self):
        result = self.advance({'service_config': 'test-client.json'})
        stages = [result['stage_review']['stage']]
        self.continue_review()
        self.assertEqual(self.advance()['state'], 'needs_evidence')
        result = self.advance({'scene_sources': [{'task_id': 'sf_test.scene0', 'revision': 0}]})
        stages.append(result['stage_review']['stage']); self.continue_review()
        result = self.advance({'evidence': str(self.evidence)})
        stages.append(result['stage_review']['stage']); self.continue_review()
        result = self.advance()
        stages.append(result['stage_review']['stage']); self.continue_review()
        result = self.advance()
        stages.append(result['stage_review']['stage']); self.continue_review()
        result = self.advance()
        self.assertEqual(stages, ['S1', 'S2', 'S3', 'S4', 'S5'])
        self.assertEqual(result['state'], 'completed')
        self.assertTrue(Path(result['outputs']['package']['public_bundle']).is_file())
        self.assertTrue(all(value == 'test-client.json' for value in self.calls))
        self.assertNotIn('stage_review', result)
        collection = result['outputs']['collection']
        self.assertEqual(collection['schema'], 'benchforge.collection-stage/v1')
        self.assertEqual(collection['collection']['records'], 1)
        self.assertEqual(collection['annotations']['items'], 1)
        self.assertTrue(Path(collection['collection_path']).is_file())

    def test_saved_annotation_operation_keeps_its_identity_and_output_path(self):
        plan = json.loads(self.plan.read_text(encoding='utf-8'))
        step = plan['steps'][1]
        step.update(id='annotations', uses='evidence.validate', artifact_type='scene.annotations')
        plan['steps'][2]['arguments']['evidence'] = '$annotations.evidence'
        write(self.plan, plan)
        self.advance({'service_config': 'test-client.json'}); self.continue_review()
        self.advance({'scene_sources': [{'task_id': 'sf_test.scene0', 'revision': 0}]}); self.continue_review()
        result = self.advance({'evidence': str(self.evidence)})
        receipt = result['steps']['annotations']['result']
        self.assertEqual(receipt['schema'], 'benchforge.annotation-stage/v1')
        self.assertTrue(Path(receipt['annotations_path']).is_file())
        self.assertNotIn('collection', result['steps'])
        for _ in range(3):
            self.continue_review()
            result = self.advance()
        self.assertEqual(result['state'], 'completed')

    def test_s1_and_s2_reviews_block_downstream_steps_even_with_all_bindings(self):
        bindings = {'service_config': 'test-client.json', 'evidence': str(self.evidence),
                    'scene_sources': [{'task_id': 'sf_test.scene0', 'revision': 0}]}
        first = self.advance(bindings)
        self.assertEqual(first['stage_review']['stage'], 'S1')
        self.assertEqual(self.advance()['steps'], {})
        self.continue_review()
        second = self.advance()
        self.assertEqual(second['stage_review']['stage'], 'S2')
        self.assertNotIn('collection', self.advance()['steps'])
        self.continue_review()
        third = self.advance()
        self.assertEqual(third['stage_review']['stage'], 'S3')
        self.assertNotIn('questions', third['steps'])

    def test_changed_annotation_source_is_blocked_before_s4(self):
        self.advance({'service_config': 'test-client.json'}); self.continue_review()
        self.advance({'scene_sources': [{'task_id': 'sf_test.scene0', 'revision': 0}]}); self.continue_review()
        self.advance({'evidence': str(self.evidence)}); self.continue_review()
        Path(self.record['provenance']['path']).write_text('{"changed":true}')
        result = self.advance()
        self.assertEqual(result['state'], 'check_failed')
        self.assertIn('annotation source changed', result['stage_review']['check_failure']['error'])
        self.assertNotIn('questions', result['steps'])

    def test_s3_rejects_cross_partition_rename_and_missing_template_quantity(self):
        self.advance({'service_config': 'test-client.json'}); self.continue_review()
        self.advance({'scene_sources': [{'task_id': 'sf_test.scene0', 'revision': 0}]}); self.continue_review()
        record = copy.deepcopy(self.record); record.update(scene_group_id='another-name', split='test')
        jsonl(self.evidence, [record])
        with self.assertRaisesRegex(ValueError, 'partition'):
            self.advance({'evidence': str(self.evidence)})
        jsonl(self.evidence, [])
        result = self.advance({'evidence': str(self.evidence)})
        self.assertEqual(result['state'], 'needs_evidence')
        self.assertEqual(result['steps']['collection']['result']['shortfall'], {'C43-T01': 1})

    def test_s3_rejects_a_fabricated_source_with_an_accepted_scene_id(self):
        self.advance({'service_config': 'test-client.json'}); self.continue_review()
        self.advance({'scene_sources': [{'task_id': 'sf_test.scene0', 'revision': 0}]}); self.continue_review()
        forged = self.root / 'forged.json'
        forged.write_text(Path(self.record['provenance']['path']).read_text())
        record = copy.deepcopy(self.record)
        record['provenance']['path'] = str(forged)
        record['provenance'].pop('derivation')
        jsonl(self.evidence, [record])
        with self.assertRaisesRegex(ValueError, 'covered by the service acceptance'):
            self.advance({'evidence': str(self.evidence)})

    def test_derived_answer_must_replay_from_native_fields(self):
        self.advance({'service_config': 'test-client.json'}); self.continue_review()
        self.advance({'scene_sources': [{'task_id': 'sf_test.scene0', 'revision': 0}]}); self.continue_review()
        file = Path(self.record['provenance']['path'])
        value = json.loads(file.read_text(encoding='utf-8'))
        value['answer']['value'] += 1
        write(file, value)
        with self.assertRaisesRegex(ValueError, 'does not match replay'):
            self.advance({'evidence': str(self.evidence)})

    def test_derived_source_dependencies_remain_frozen_after_s3(self):
        self.advance({'service_config': 'test-client.json'}); self.continue_review()
        self.advance({'scene_sources': [{'task_id': 'sf_test.scene0', 'revision': 0}]}); self.continue_review()
        self.advance({'evidence': str(self.evidence)}); self.continue_review()
        write(self.native_source, {'distance': 1, 'speed': 1})
        result = self.advance()
        self.assertEqual(result['state'], 'check_failed')
        self.assertIn('annotation source changed', result['stage_review']['check_failure']['error'])
        self.assertNotIn('questions', result['steps'])

    def test_robot_task_stops_at_s3_without_a_native_robot_episode(self):
        plan = json.loads(self.plan.read_text(encoding='utf-8'))
        plan['assessment']['annotations'] = {'require_robot_locomotion': True}
        write(self.plan, plan)
        self.advance({'service_config': 'test-client.json'}); self.continue_review()
        self.advance({'scene_sources': [{'task_id': 'sf_test.scene0', 'revision': 0}]}); self.continue_review()
        result = self.advance({'evidence': str(self.evidence)})
        self.assertEqual(result['state'], 'needs_evidence')
        self.assertEqual(result['steps']['collection']['result']['stage'], 'S3')
        self.assertNotIn('questions', result['steps'])
        self.assertNotIn('package', result['steps'])


if __name__ == '__main__':
    unittest.main()
