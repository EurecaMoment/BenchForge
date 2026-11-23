"""Regression coverage for physical advisories and frozen S2 re-review."""
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

from capabilities.scene_authoring.geometry.relations import native_part_bounds
from capabilities.scene_authoring.validation.acceptance import (
    SCENE_ACCEPTANCE_POLICY_VERSION, VISUAL_CHECKS, capture_acceptance,
)
from capabilities.scene_authoring.validation.support_relations import support_check, support_path
from pipelines.scene_production.lifecycle import SceneLifecycle
from pipelines.scene_production.review import review_scene


class AcceptancePolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.task = {'id': 'sf_test.scene0', 'run_id': 'sf_test', 'unit': {
            'revision': 0, 'phase': 'review', 'capture_token': 'token',
            'intent': {'execution_mode': 'production', 'capture_only': True}}}
        self.directory = self.root / self.task['id'] / 'revision_0'
        self.capture = self.directory / 'capture'
        self.capture.mkdir(parents=True)
        self.program = {'objects': [{'id': identity, 'dynamic': False, 'support': 'ground'}
                                    for identity in ('cabinet', 'decor')], 'cameras': [{}]}
        self.report = {'status': 'captured', 'renderable': True, 'token': 'token',
                       'physics': {'stable': True, 'steps': 60, 'dt': .01}}
        self.evidence = {'origin': 'isaac_native', 'token': 'token',
                         'physics': self.report['physics'], 'frames': [{'frame_id': 'view_0'}],
                         'entities': {
                             identity: {'world_aabb': {'min': [0, y, 0], 'max': [1, y + 1, 1]},
                                        'collision': {'paths': ['/' + identity]}}
                             for identity, y in [('cabinet', 0), ('decor', .9)]}}
        self.brief = {'name': 'Test room', 'summary': 'Room with a cabinet.',
                      'object_roles': [], 'physical_assumptions': [],
                      'hard_requirements': [], 'relations': []}
        self.admission = {'passed': True, 'scene_brief': self.brief}
        self.observation = {'view': 'view_0.png', 'observation': 'The cabinet is upright against the wall.'}
        self.review = {'acceptable': True, 'visual_realism': {'status': 'visually_plausible'},
                       'optional_suggestions': ['Tone down cabinet gloss'],
                       'life_review': {'checks': [
                           {'check': key, 'passed': True, 'evidence': [self.observation]}
                           for key in VISUAL_CHECKS]},
                       'brief_alignment': {'passed': True, 'hard_requirements_preserved': True,
                                           'unresolved': [], 'observations': [self.observation],
                                           'requirement_checks': [], 'relation_checks': []}}
        (self.capture / 'view_0.png').write_bytes(b'native-view-fixture')

    def accept(self):
        with patch('capabilities.scene_authoring.validation.acceptance._policy_for_mode', return_value={'enabled': True}), \
                patch('capabilities.scene_authoring.validation.acceptance.admit_program', return_value=self.admission):
            return capture_acceptance(self.root, self.program, self.report, self.evidence, self.review, self.capture)

    def test_static_bounds_are_advisory_for_a_visible_usable_scene(self):
        result = self.accept()
        self.assertTrue(result['passed'], result['issues'])
        self.assertEqual(result['physical_issues'], [])
        self.assertIn('cabinet/decor', result['physical_advisories'][0])
        self.review['life_review']['checks'][0]['passed'] = False
        self.assertFalse(self.accept()['passed'])

    def test_dynamic_broad_phase_overlap_does_not_override_clearance_evidence(self):
        self.program['objects'][1]['dynamic'] = True
        self.brief['relations'] = [{'type': 'clearance', 'source': 'cabinet', 'target': 'decor'}]
        alignment = self.review['brief_alignment']
        alignment['relation_checks'] = [{'index': 0, 'passed': False, **self.observation}]
        result = self.accept()
        self.assertEqual(result['physical_issues'], [])
        self.assertTrue(result['physical_advisories'])
        self.assertFalse(result['passed'])
        alignment['relation_checks'][0].update(passed=True, observation='The prop footprint leaves the aisle open.')
        self.assertTrue(self.accept()['passed'])
        self.evidence['physics']['stable'] = False
        self.assertFalse(self.accept()['passed'])

    def test_partial_support_bounds_defer_to_evidence_but_measured_gaps_block(self):
        self.program['objects'][1]['support'] = 'cabinet'
        bounds = self.evidence['entities']['decor']['world_aabb']
        bounds.update(min=[0, .2, 1], max=[1, 1.1, 2])
        self.brief['relations'] = [{'type': 'supported_by', 'source': 'decor', 'target': 'cabinet'}]
        result = self.accept()
        self.assertFalse(result['passed'])
        self.assertIn('native bounds do not verify this support', result['physical_advisories'][0])
        self.assertIsNone(result['native_relation_checks'][0]['passed'])
        self.assertTrue(result['native_relation_checks'][0]['visual_check_required'])
        self.brief['hard_requirements'] = ['The prop must contact the cabinet during the task.']
        alignment = self.review['brief_alignment']
        alignment['relation_checks'] = [{'index': 0, 'passed': True, 'view': 'view_0.png',
                                        'observation': 'The underside rests on the cabinet top.'}]
        alignment['requirement_checks'] = [{'index': 0, 'passed': False, **self.observation}]
        self.assertFalse(self.accept()['passed'])
        alignment['requirement_checks'][0].update(passed=True, observation='The underside rests on the cabinet.')
        self.assertTrue(self.accept()['passed'])
        self.program['objects'][1]['dynamic'] = True
        self.assertFalse(self.accept()['passed'])
        self.program['objects'][1]['dynamic'] = False
        self.brief['relations'][0]['requires_measurement'] = True
        self.assertFalse(self.accept()['passed'])
        bounds['min'][2] = 1.2
        self.assertIn('measured gap', self.accept()['physical_issues'][0])

    def test_ground_support_follows_and_measures_each_hop(self):
        self.program['objects'][1]['support'] = 'cabinet'
        bounds = self.evidence['entities']['decor']['world_aabb']
        bounds.update(min=[.1, .1, 1], max=[.9, .9, 2])
        self.brief['relations'] = [{'type': 'supported_by', 'source': 'decor', 'target': 'ground'}]
        result = self.accept()
        self.assertTrue(result['passed'], result['issues'])
        relation = result['native_relation_checks'][0]
        self.assertIs(relation['passed'], True)
        self.assertEqual([(hop['source'], hop['target'], hop['passed']) for hop in relation['support_hops']],
                         [('decor', 'cabinet', True), ('cabinet', 'ground', True)])
        bounds['min'][2] = 1.2
        self.assertIs(self.accept()['native_relation_checks'][0]['passed'], False)

    def test_shared_ground_does_not_confirm_support_by_another_assembly(self):
        self.program['objects'].append({'id': 'shelf', 'dynamic': False, 'support': 'ground'})
        self.program['objects'][1]['support'] = 'shelf'
        self.evidence['entities']['shelf'] = {'world_aabb': {'min': [0, 0, 0], 'max': [1, 1, 1]},
                                               'collision': {'paths': ['/shelf']}}
        self.evidence['entities']['decor']['world_aabb'].update(min=[.1, .1, 1], max=[.9, .9, 2])
        self.brief['relations'] = [{'type': 'supported_by', 'source': 'decor', 'target': 'cabinet'}]
        self.assertIsNone(self.accept()['native_relation_checks'][0]['passed'])
        checks = self.review['brief_alignment']['relation_checks']
        checks.append({'index': 0, 'passed': True, 'view': 'view_0.png',
                       'observation': 'Box base rests on the cabinet shelf spanning the two visible side posts.'})
        self.assertTrue(self.accept()['passed'])
        checks[0]['view'] = 'view_99.png'
        self.assertFalse(self.accept()['passed'])
        checks[0].update(view='view_0.png', passed=False, advisory=True)
        self.assertFalse(self.accept()['passed'])

    def test_support_lookup_is_directed_and_terminates_in_a_cycle(self):
        objects = {'a': {'support': 'b'}, 'b': {'support': 'a'},
                   'mount': {'support': 'ground', 'metadata': {'spatial_attachment': {'target': 'a'}}}}
        self.assertEqual(support_path(objects, 'a', 'ground'), ['a', 'b'])
        self.assertEqual(support_path(objects, 'mount', 'ground'), ['mount'])
        self.assertIsNone(support_check(objects, 'a', 'ground', {('a', 'b'): True})[0])

    def test_explicit_lighting_requirement_still_needs_positive_evidence(self):
        self.brief['hard_requirements'] = ['Observe the shadow of the angled light on the cabinet.']
        alignment = self.review['brief_alignment']
        alignment['requirement_checks'] = [{'index': 0, 'passed': False, 'advisory': True, **self.observation}]
        self.assertFalse(self.accept()['passed'])
        alignment['requirement_checks'][0].update(passed=True, observation='The angled light casts a side shadow.')
        self.assertTrue(self.accept()['passed'])

    def test_compose_rotation_before_bounding_the_primitive(self):
        angle = math.sqrt(.5)
        entity = {'world_transform': [[angle, -angle, 0, 0], [angle, angle, 0, 0],
                                      [0, 0, 1, 0], [0, 0, 0, 1]],
                  'geometry_parts': {'source': 'executed_primitive_construction',
                                     'coordinate_frame': 'entity_local_meters', 'parts': [{
                                         'prim_path': '/part', 'shape': 'box', 'offset': [0, 0, 0],
                                         'size': [2, .1, .1], 'rotation_deg_xyz': [0, 0, 45]}]}}
        _, high = native_part_bounds(entity)[0]
        self.assertAlmostEqual(high[0], 1)
        self.assertAlmostEqual(high[1], .05)

    def write_old_review(self):
        for relative, value in [('program.json', self.program), ('capture/report.json', self.report),
                                ('capture/evidence.json', self.evidence), ('scene_admission.json', self.admission),
                                ('scene_review.json', {'passed': False})]:
            (self.directory / relative).write_text(json.dumps(value), encoding='utf-8')
        versions = self.accept()['evidence_versions']
        self.old_receipt = {'passed': False, 'enforced': True, 'capture_token': 'token',
                            'policy_version': 2,
                            'evidence_versions': versions}
        (self.directory / 'scene_acceptance.json').write_text(json.dumps(self.old_receipt), encoding='utf-8')

    def test_review_only_consumes_original_evidence_and_preserves_prior_receipt(self):
        self.write_old_review()
        (self.capture / 'view_untracked.png').write_bytes(b'untracked pixels')
        store = Mock(root=self.root)
        store.directory.return_value = self.directory
        store.snapshot.return_value = {'state': 'PENDING'}
        with patch('pipelines.scene_production.lifecycle.policy_for', return_value={'enabled': True}), \
                patch('pipelines.scene_production.lifecycle.admit_program', return_value=self.admission):
            self.task['unit'] = SceneLifecycle(self.root).retry(self.task, None, 'review_only', store.directory)
        unit = self.task['unit']
        self.assertEqual((unit['phase'], unit['revision'], unit['capture_token']), ('review', 0, 'token'))
        self.assertEqual(unit['scene_review_policy_version'], SCENE_ACCEPTANCE_POLICY_VERSION)

        with patch('pipelines.scene_production.review._strict_policy', return_value={'enabled': True}), \
                patch('capabilities.scene_authoring.validation.acceptance._policy_for_mode', return_value={'enabled': True}), \
                patch('capabilities.scene_authoring.validation.acceptance.admit_program', return_value=self.admission), \
                patch('pipelines.scene_production.review.completion', return_value=self.review) as model, \
                patch('pipelines.scene_production.review.audit_scene', return_value={}):
            review_scene(store, self.task, Event(), capture_only=True)

        self.assertEqual(model.call_args.args[1].name, f'policy_{SCENE_ACCEPTANCE_POLICY_VERSION}')
        self.assertEqual(model.call_args.args[2], [self.capture / 'view_0.png'])
        archive = self.directory / 'previous_scene_review' / f'before_policy_{SCENE_ACCEPTANCE_POLICY_VERSION}'
        self.assertEqual(json.loads((archive / 'scene_acceptance.json').read_text()), self.old_receipt)
        self.assertFalse(json.loads((archive / 'scene_review.json').read_text())['passed'])
        self.assertTrue(json.loads((self.directory / 'scene_acceptance.json').read_text())['passed'])
        self.assertEqual(store.update.call_args.kwargs['state'], 'SUCCEEDED')
        self.assertEqual((self.capture / 'view_0.png').read_bytes(), b'native-view-fixture')

    def test_review_only_rejects_changed_native_evidence_and_current_policy(self):
        self.write_old_review()
        lifecycle = SceneLifecycle(self.root)
        unit = self.task['unit']
        with patch('pipelines.scene_production.lifecycle.policy_for', return_value={'enabled': True}), \
                patch('pipelines.scene_production.lifecycle.admit_program', return_value=self.admission):
            current = {**self.old_receipt, 'policy_version': SCENE_ACCEPTANCE_POLICY_VERSION}
            (self.directory / 'scene_acceptance.json').write_text(json.dumps(current), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'older scene policy'):
                lifecycle.retry(self.task, None, 'review_only', Mock())
            self.write_old_review()
            (self.capture / 'view_0.png').write_bytes(b'changed pixels')
            with self.assertRaisesRegex(ValueError, 'Scene changed'):
                lifecycle.retry(self.task, None, 'review_only', Mock())
        self.assertNotIn('scene_review_policy_version', unit)
