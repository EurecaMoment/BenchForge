"""Physical failures must not be reported as visual judgments never performed."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from capabilities.scene_authoring.validation.acceptance import capture_acceptance


class SceneReviewScopeTests(unittest.TestCase):
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
