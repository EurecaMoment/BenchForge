"""Static v2 usage guidance; no live catalog or deployment inspection."""
import unittest

from capabilities.isaac_sim.guide import usage


class GuideTests(unittest.TestCase):
    # 指南返回完整 v2 操作集合与幂等/校验说明。
    def test_usage_lists_the_v2_operations(self):
        result = usage()
        self.assertEqual(result['protocol'], 'isaacsim_rpc.v1.SimulationService')
        self.assertEqual(set(result['operations']),
                         {'health', 'submit', 'status', 'wait', 'cancel', 'download'})
        self.assertTrue(any('idempotent' in note for note in result['notes']))
        self.assertTrue(any('sha256' in note for note in result['notes']))

    # 指定操作只返回该项；未知操作明确拒绝。
    def test_one_operation_can_be_selected(self):
        result = usage('submit')
        self.assertEqual(list(result['operations']), ['submit'])
        self.assertIn('SimulateRequest', result['operations']['submit']['request']['simulation'])
        with self.assertRaisesRegex(ValueError, 'Unknown Isaac Sim operation'):
            usage('capabilities')


if __name__ == '__main__':
    unittest.main()
