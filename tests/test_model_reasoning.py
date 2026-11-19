"""The scene reviewer honors operator model settings across the startup boundary."""
import io
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from service_access.openai_client import completion
from service_access.runtime_config import apply_runtime_environment, load_service_config


class ModelReasoningTests(unittest.TestCase):
    # 验证配置的推理强度传至有界评审请求，且不会关闭模型思考。
    def test_configured_effort_reaches_bounded_review_without_disabling_thinking(self):
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {}, clear=True):
            root = Path(temporary)
            models = root / 'models.json'
            models.write_text(json.dumps({'providers': {'FastQwen': {
                'model': 'Qwen3.8-Flash-Next', 'url': 'http://127.0.0.1:8000/v1',
                'reasoning_effort': 'xhigh'}}}))
            config = root / 'service.json'
            config.write_text(json.dumps({'execution_mode': 'exploration', 'models_config': 'models.json'}))
            apply_runtime_environment(load_service_config(config, required_tokens=()))
            bodies = []
            # 捕获模型请求正文并返回最小流式 JSON 完成事件，供推理参数断言。
            def respond(request, **_):
                bodies.append(json.loads(request.data))
                event = {'choices': [{'delta': {'content': '{"passed":true}'}, 'finish_reason': 'stop'}]}
                return io.BytesIO(('data: ' + json.dumps(event) + '\n\ndata: [DONE]\n').encode())
            with patch('service_access.openai_client.urllib.request.urlopen', side_effect=respond):
                self.assertEqual(completion('Review this scene', root / 'review', bounded_review=True), {'passed': True})
            self.assertEqual(bodies[0]['reasoning_effort'], 'xhigh')
            self.assertTrue(bodies[0]['chat_template_kwargs']['enable_thinking'])
            self.assertGreater(bodies[0]['max_tokens'], 10000)
            apply_runtime_environment({})
            self.assertNotIn('BENCHFORGE_MODEL_REASONING_EFFORT', os.environ)
