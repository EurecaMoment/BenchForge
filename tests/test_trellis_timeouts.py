import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import Mock, patch

from PIL import Image

from service_access.http_tools import ModelClient


class TrellisTimeoutTests(unittest.TestCase):
    # 使用独立回执目录，重复执行同一份物体重建请求。
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        image = self.work / 'reference.png'
        Image.new('RGB', (8, 8)).save(image)
        self.request = {'output_dir': self.work, 'objects': [
            {'object_id': 'cup', 'image': image, 'mask': image, 'seed': 1}]}
        self.client = ModelClient('FastTrellis2', {
            'url': 'http://localhost/v1', 'model': 'trellis', 'timeout_seconds': 1800})
        self.stop = Event()

    # 提交使用部署的超时，受理未知时重入不会再次提交。
    def test_submission_timeout_keeps_unknown_receipt(self):
        with patch('service_access.http_tools.urlopen', side_effect=TimeoutError) as send:
            for _ in range(2):
                with self.assertRaisesRegex(RuntimeError, 'Acceptance is unknown'):
                    self.client.trellis(self.request, self.work, self.stop)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.kwargs['timeout'], 1800)
        receipt = json.loads((self.work / 'cup.job.json').read_text())
        self.assertEqual(receipt['state'], 'submission_unknown')

    # 持有旧版回执时，查询暂时超时后继续用原 ID 取回结果。
    @patch('service_access.http_tools.time.sleep')
    def test_poll_timeout_resumes_existing_job(self, sleep):
        receipt = self.work / 'cup.job.json'
        receipt.write_text('{"id": "original"}')
        self.client._json = Mock(side_effect=[
            TimeoutError(), {'status': 'completed', 'data': [{'file_id': 'mesh'}]}])
        self.client._download = Mock()
        self.client.trellis(self.request, self.work, self.stop)
        self.assertEqual([c.args for c in self.client._json.call_args_list],
                         [('objects/generations/original',)] * 2)
        self.assertEqual(json.loads(receipt.read_text()), {'id': 'original', 'state': 'completed'})

    # 连续查询超时受到预算约束，原回执仍可恢复。
    @patch('service_access.http_tools.time.sleep')
    @patch('service_access.http_tools.time.monotonic', side_effect=[0, 0, 1800, 1800])
    def test_poll_budget_exhaustion_keeps_job_id(self, clock, sleep):
        receipt = self.work / 'cup.job.json'
        receipt.write_text('{"id": "original"}')
        self.client._json = Mock(side_effect=TimeoutError)
        with self.assertRaisesRegex(TimeoutError, 'job original'):
            self.client.trellis(self.request, self.work, self.stop)
        self.assertEqual(self.client._json.call_count, 1)
        self.assertEqual(json.loads(receipt.read_text()), {'id': 'original'})
