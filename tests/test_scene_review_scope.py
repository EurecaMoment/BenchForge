"""Physical failures must not be reported as visual judgments never performed."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import json
from threading import Event

from capabilities.scene_authoring.validation.acceptance import capture_acceptance
from pipelines.scene_production.review import review_scene
from test_scene_diagnostics import sample


class SceneReviewScopeTests(unittest.TestCase):
    # 验证物理检查失败后仍收集视觉缺陷，且该场景不能被当作成功导出。
    def test_physical_failure_still_collects_visual_defects_and_cannot_export(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'sf_test.scene0' / 'revision_0'
            capture = directory / 'capture'
            capture.mkdir(parents=True)
            (directory / 'program.json').write_text(json.dumps(sample()), encoding='utf-8')
            report = {'status': 'captured', 'renderable': True, 'physics': {'stable': False}}
            (capture / 'report.json').write_text(json.dumps(report), encoding='utf-8')
            (capture / 'evidence.json').write_text('{"frames":[]}', encoding='utf-8')
            (capture / 'view_0.png').write_bytes(b'fixture-pixels')
            updates = []

            class Store:
                # 将评审器的产物访问指向本测试的固定修订目录。
                def directory(self, *_):
                    return directory

                # 捕获评审器写回的状态和失败信息，供拒绝导出断言。
                def update(self, *args, **kwargs):
                    updates.append(kwargs)

            store = Store()
            store.root = root
            task = {'id': 'sf_test.scene0', 'run_id': 'sf_test', 'unit': {
                'revision': 0, 'intent': {'execution_mode': 'production'}}}
            rejection = {'enforced': True, 'passed': False,
                         'issues': ['native support missing'], 'physical_issues': ['native support missing']}
            with patch('pipelines.scene_production.review._strict_policy', return_value={'enabled': True}), \
                    patch('pipelines.scene_production.review.capture_acceptance', return_value=rejection), \
                    patch('pipelines.scene_production.review.completion', return_value={
                        'acceptable': False, 'issues': ['cat visibly faces the wrong direction']}) as reviewer, \
                    patch('pipelines.scene_production.review.audit_scene', return_value={}), \
                    patch('pipelines.scene_production.review.produce') as export:
                review_scene(store, task, Event())
            reviewer.assert_called_once()
            prompt, _, images = reviewer.call_args.args[:3]
            self.assertIn('native support missing', prompt)
            self.assertEqual(images, [capture / 'view_0.png'])
            saved = json.loads((directory / 'scene_review.json').read_text(encoding='utf-8'))
            self.assertIn('cat visibly faces the wrong direction', saved['issues'])
            self.assertIn('native support missing', saved['issues'])
            self.assertFalse(saved['passed'])
            self.assertEqual(updates[-1]['state'], 'FAILED_FINAL')
            export.assert_not_called()

    # 验证预检查失败保留真实失败原因，不凭空补造视觉缺陷。
    def test_failed_precheck_keeps_failure_without_inventing_six_visual_defects(self):
        with TemporaryDirectory() as temporary, \
                patch('capabilities.scene_authoring.validation.acceptance._policy_for_mode', return_value={'enabled': True}), \
                patch('capabilities.scene_authoring.validation.acceptance.admit_program', side_effect=ValueError('Robot asset is missing')):
            receipt = capture_acceptance(Path(temporary), {'objects': [], 'cameras': []}, {}, {},
                                         {'visual_review_skipped': True}, Path(temporary) / 'capture')
        self.assertFalse(receipt['passed'])
        self.assertEqual(receipt['visual_status'], 'not_assessed')
        self.assertIn('Robot asset is missing', receipt['physical_issues'])
        self.assertFalse(any('Visual/life check failed' in issue for issue in receipt['issues']))
