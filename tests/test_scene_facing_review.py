"""Explicit facing relations need a visible, directed functional front."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from capabilities.scene_authoring.validation.acceptance import VISUAL_CHECKS, capture_acceptance


class FacingReviewTests(unittest.TestCase):
    # 固定家具位置和尺寸，仅改变朝向观察；同时验证普通邻接关系仍接受原回执格式。
    def test_single_direction_facing_in_production_acceptance(self):
        relation = {'source': 'sofa', 'target': 'tv', 'type': 'facing'}
        program = {'objects': [{'id': oid, 'kind': 'native', 'support': 'ground'}
                               for oid in ('sofa', 'tv')], 'cameras': [{}]}
        report = {'status': 'captured', 'renderable': True, 'token': 'frame-set'}
        frame = {'frame_id': 'view_0', 'objects': [{'object_id': 'sofa'}]}
        evidence = {'origin': 'isaac_native', 'token': report['token'],
                    'physics': {'stable': True, 'steps': 60, 'dt': .01}, 'frames': [frame],
                    'entities': {oid: {'world_aabb': {'min': [0, y, 0], 'max': [1, y + 1, 1]},
                                       'collision': {'paths': ['/' + oid]}}
                                 for oid, y in [('sofa', 0), ('tv', 3)]}}
        observation = {'view': 'view_0.png', 'observation': 'Side view of the sofa.'}
        alignment = {'passed': True, 'hard_requirements_preserved': True, 'unresolved': [],
                     'observations': [observation], 'requirement_checks': []}
        review = {'acceptable': True, 'visual_realism': {'status': 'visually_plausible'},
                  'brief_alignment': alignment, 'life_review': {'checks': [
                      {'check': key, 'passed': True, 'evidence': [observation]} for key in VISUAL_CHECKS]}}
        admission = {'scene_brief': {'hard_requirements': [], 'relations': [relation]}}
        cue = {'front_cue': 'Seat cushion ahead of the raised backrest.'}
        with TemporaryDirectory() as temporary, \
             patch('capabilities.scene_authoring.validation.acceptance._policy_for_mode', return_value={'enabled': True}), \
             patch('capabilities.scene_authoring.validation.acceptance.admit_program', return_value=admission):
            capture = Path(temporary) / 'capture'
            capture.mkdir()
            (capture / 'view_0.png').write_bytes(b'fixture')
            for kind, fields, visible, passes in [
                    ('facing', {}, True, False),
                    ('facing', {**cue, 'front_to_target': 'away'}, True, False),
                    ('facing', {**cue, 'front_to_target': 'unclear'}, True, False),
                    ('facing', {**cue, 'front_to_target': 'toward'}, False, False),
                    ('facing', {**cue, 'front_to_target': 'toward'}, True, True),
                    ('near', {}, True, True)]:
                with self.subTest(kind=kind, fields=fields, visible=visible):
                    relation['type'] = kind
                    frame['objects'] = [{'object_id': 'sofa' if visible else 'tv'}]
                    alignment['relation_checks'] = [{'index': 0, 'passed': True, **observation, **fields}]
                    result = capture_acceptance(Path(temporary), program, report, evidence, review, capture)
                    self.assertEqual(result['physical_issues'], [])
                    self.assertEqual(result['passed'], passes, result['issues'])
