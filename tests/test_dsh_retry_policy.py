"""Malformed model responses use bounded provider retries in BenchForge presets."""
import json
from pathlib import Path
import tempfile
import unittest

from dsh.presets.build_preset import model_patches


class DshRetryPolicyTests(unittest.TestCase):
    def test_malformed_responses_get_two_bounded_retries_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'benchforge.json'
            config.write_text(json.dumps({'providers': {'FastQwen': {
                'model': 'qwen-flash-next', 'url': 'http://127.0.0.1:18041/v1',
            }}}), encoding='utf-8')

            patches = model_patches(config)

        provider = next(row for row in patches if row['id'] == 'llm-pi-ai')
        self.assertEqual(provider['config']['providers']['benchforge-models']['retryPolicy'], {
            'mode': 'normal', 'maxRetries': 2,
            'retryableCodes': [
                'EMPTY_RESPONSE', 'MALFORMED_RESPONSE', 'RATE_LIMIT', 'SERVER', 'TIMEOUT', 'TRANSPORT',
            ],
        })

    def test_explicit_retry_budget_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'benchforge.json'
            config.write_text(json.dumps({'providers': {'DSpark4A100': {
                'model': 'deepseek', 'url': 'http://127.0.0.1:18041/v1', 'retries': 4,
            }}}), encoding='utf-8')

            patches = model_patches(config)

        provider = next(row for row in patches if row['id'] == 'llm-pi-ai')
        policy = provider['config']['providers']['benchforge-models']['retryPolicy']
        self.assertEqual(policy['maxRetries'], 4)
        self.assertIn('MALFORMED_RESPONSE', policy['retryableCodes'])


if __name__ == '__main__':
    unittest.main()
