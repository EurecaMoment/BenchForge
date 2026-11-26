"""isaacsim_rpc.v1 transport and durable-job contract against the local mock.

The mock service binds 127.0.0.1:18500 by default (the port used by
config/benchforge.local.json) and falls back to an ephemeral port only when
that port is already occupied, so the suite stays runnable on a busy host.
"""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import grpc

from capabilities.isaac_sim.grpc import simulation_pb2 as pb
from capabilities.isaac_sim.grpc.tasks import DurableJobs, request_id
from capabilities.isaac_sim.grpc.transport import (
    RemoteJobFailed, SimulationClient, document, job_state, poll_interval,
)
from capabilities.isaac_sim.mock_grpc_service import (
    MockSimulationService, serve_mock, simulation_config,
)


# 构造最小可提交仿真请求，供传输与幂等测试复用。
def simulation(*, body=True, steps=3, frames=(1, 2, 3)):
    bodies = [{'id': 'cube', 'shape': 'cube',
               'position': {'x': 0.0, 'y': 0.0, 'z': 0.5},
               'orientation_xyzw': {'x': 0.0, 'y': 0.0, 'z': 0.0, 'w': 1.0},
               'size': {'x': 0.2, 'y': 0.2, 'z': 0.2},
               'color': {'x': 0.8, 'y': 0.1, 'z': 0.1}, 'mass': 1.0}] if body else []
    return {'bodies': bodies, 'steps': steps, 'dt': 1 / 60,
            'render': {'size': 64, 'spp': 8}, 'capture': {'frames': list(frames)}}


class SimulationTransportTests(unittest.TestCase):
    # 复用单个 mock 服务进程，优先占用部署端口 18500。
    @classmethod
    def setUpClass(cls):
        cls.server, cls.port, cls.servicer = serve_mock(MockSimulationService(), port=18500,
                                                        fallback=True)

    @classmethod
    def tearDownClass(cls):
        cls.server.stop(0)
        cls.servicer.cleanup()

    # 每个用例重置 mock 状态并准备独立回执目录。
    def setUp(self):
        self.servicer.reset()
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.config = simulation_config(self.port)

    # 建立带超时的客户端，并确保测试结束关闭通道。
    def client(self, **options):
        client = SimulationClient(simulation_config(self.port, **options))
        self.addCleanup(client.close)
        return client

    # 验证健康检查读取契约版本与可回收磁盘信息。
    def test_health_reports_contract_version_and_capacity(self):
        health = document(self.client().health())
        self.assertEqual(health['status'], 'ready')
        self.assertEqual(health['api_version'], 'isaacsim_rpc.v1')
        self.assertGreater(int(health['artifacts_free_bytes']), 0)

    # 验证同一 request_id 与载荷幂等，载荷变化明确冲突。
    def test_submit_is_idempotent_and_rejects_changed_payload(self):
        client = self.client()
        first = client.submit(simulation(), 'request-a')
        second = client.submit(simulation(), 'request-a')
        self.assertEqual(first.id, second.id)
        self.assertEqual(self.servicer.submissions, 2)
        with self.assertRaises(grpc.RpcError) as rejected:
            client.submit(simulation(steps=4), 'request-a')
        self.assertEqual(rejected.exception.code(), grpc.StatusCode.ALREADY_EXISTS)

    # 验证应答丢失后可用同一 identity 重试且只产生一个任务。
    def test_lost_reply_is_retried_with_the_same_identity(self):
        self.servicer.lose_reply = True
        jobs = DurableJobs(self.client(), self.root / 'jobs', 'scene-a')
        with patch('time.sleep'):
            job = jobs.submit('view-0', simulation())
        self.assertEqual(len(self.servicer.jobs), 1)
        self.assertEqual(self.servicer.submissions, 2)
        frozen = json.loads((self.root / 'jobs/view-0/request.json').read_text())
        self.assertEqual(frozen['request_id'], request_id('scene-a', 'view-0'))
        self.assertEqual(frozen['protocol'], 'isaacsim_rpc.v1.SimulationService')

    # 验证已持久回执在重启后复用，而不是重复提交。
    def test_receipt_is_reused_without_resubmitting(self):
        first = DurableJobs(self.client(), self.root / 'jobs', 'scene-a').submit('view-0', simulation())
        submissions = self.servicer.submissions
        second = DurableJobs(self.client(), self.root / 'jobs', 'scene-a').submit('view-0', simulation())
        self.assertEqual(first.id, second.id)
        self.assertEqual(self.servicer.submissions, submissions)

    # 验证同一回执键不能绑定另一个载荷。
    def test_changed_payload_for_the_same_key_is_rejected(self):
        jobs = DurableJobs(self.client(), self.root / 'jobs', 'scene-a')
        jobs.submit('view-0', simulation())
        with self.assertRaisesRegex(ValueError, 'request changed'):
            jobs.submit('view-0', simulation(steps=9))

    # 验证轮询在状态变化时持久化回执并最终返回成功任务。
    def test_wait_polls_and_persists_progress(self):
        self.servicer.submit_state = 'running'
        self.servicer.status_sequence = ['running', 'succeeded']
        states = []
        jobs = DurableJobs(self.client(), self.root / 'jobs', 'scene-a',
                           on_status=lambda job: states.append(job_state(job)))
        job = jobs.submit('view-0', simulation())
        with patch('time.sleep'):
            jobs.wait('view-0', job)
        self.assertIn('running', states)
        self.assertEqual(states[-1], 'succeeded')
        receipt = json.loads((self.root / 'jobs/view-0/receipt.json').read_text())
        self.assertEqual(receipt['state'], 'succeeded')

    # 验证终态失败抛出保留完整任务文档的专用异常。
    def test_wait_raises_remote_failure_with_the_job_document(self):
        self.servicer.submit_state = 'running'
        self.servicer.status_sequence = ['failed']
        self.servicer.error_text = 'solver diverged'
        jobs = DurableJobs(self.client(), self.root / 'jobs', 'scene-a')
        job = jobs.submit('view-0', simulation())
        with patch('time.sleep'), self.assertRaises(RemoteJobFailed) as caught:
            jobs.wait('view-0', job)
        self.assertEqual(caught.exception.job['state'], 'failed')
        self.assertEqual(caught.exception.job['error'], 'solver diverged')

    # 验证下载流式写入、逐文件校验 sha256 并保存清单。
    def test_download_streams_and_verifies_manifest(self):
        client = self.client(chunk_bytes=1024)
        jobs = DurableJobs(client, self.root / 'jobs', 'scene-a')
        job = jobs.submit('view-0', simulation())
        files = jobs.download('view-0', job)
        self.assertEqual(len(files), len(job.artifacts))
        for item in files:
            data = Path(item['source_path']).read_bytes()
            self.assertEqual(len(data), item['size_bytes'])
            self.assertEqual(hashlib.sha256(data).hexdigest(), item['sha256'])
        manifest = json.loads((self.root / 'jobs/view-0/artifacts/files.json').read_text())
        self.assertEqual(manifest['job_id'], job.id)

    # 验证已有 partial 文件时从校验偏移续传，而不是重下全部字节。
    def test_download_resumes_from_the_partial_offset(self):
        client = self.client(chunk_bytes=1024)
        jobs = DurableJobs(client, self.root / 'jobs', 'scene-a')
        job = jobs.submit('view-0', simulation())
        item = job.artifacts[1]
        directory = self.root / 'jobs/view-0/artifacts'
        target = directory / item.artifact_id / item.name
        target.parent.mkdir(parents=True)
        partial = target.parent / ('.' + target.name + '.partial')
        blob = self.servicer.blobs[item.artifact_id]
        partial.write_bytes(blob[:100])
        jobs.download('view-0', job)
        offsets = [call[2] for call in self.servicer.download_calls
                   if call[1] == item.artifact_id]
        self.assertEqual(offsets, [100])
        self.assertEqual(target.read_bytes(), blob)

    # 验证清单摘要不匹配时拒绝发布被篡改的产物。
    def test_download_rejects_sha256_mismatch(self):
        client = self.client(chunk_bytes=1024)
        jobs = DurableJobs(client, self.root / 'jobs', 'scene-a')
        job = jobs.submit('view-0', simulation())
        job.artifacts[0].sha256 = '0' * 64
        with self.assertRaisesRegex(ValueError, 'sha256'):
            jobs.download('view-0', job)

    # 验证缺少产物清单的成功任务不能静默发布空采集。
    def test_download_rejects_a_missing_manifest(self):
        client = self.client()
        jobs = DurableJobs(client, self.root / 'jobs', 'scene-a')
        job = jobs.submit('view-0', simulation())
        del job.artifacts[:]
        with self.assertRaisesRegex(ValueError, 'no artifact manifest'):
            jobs.download('view-0', job)

    # 验证超过清单长度的 partial 文件被视为不可信状态。
    def test_download_rejects_oversized_partial(self):
        client = self.client()
        jobs = DurableJobs(client, self.root / 'jobs', 'scene-a')
        job = jobs.submit('view-0', simulation())
        item = job.artifacts[0]
        target = self.root / 'jobs/view-0/artifacts' / item.artifact_id / item.name
        target.parent.mkdir(parents=True)
        (target.parent / ('.' + target.name + '.partial')).write_bytes(b'x' * (item.size_bytes + 8))
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            jobs.download('view-0', job)

    # 验证取消与确认释放都回传服务端确认。
    def test_cancel_and_ack_round_trip(self):
        self.servicer.submit_state = 'running'
        client = self.client()
        job = client.submit(simulation(), 'request-a')
        cancelled = client.cancel(job.id)
        self.assertEqual(job_state(cancelled), 'cancelled')
        released = client.ack(job.id)
        self.assertTrue(released.released)
        self.assertGreater(released.freed_bytes, 0)
        self.assertIn(job.id, self.servicer.acked)

    # 验证自适应轮询间隔先密后疏。
    def test_poll_interval_is_adaptive(self):
        self.assertEqual(poll_interval(0), 0.5)
        self.assertEqual(poll_interval(9.9), 0.5)
        self.assertEqual(poll_interval(10), 2.0)
        self.assertEqual(poll_interval(59.9), 2.0)
        self.assertEqual(poll_interval(60), 5.0)

    # 验证状态字符串大小写不敏感。
    def test_job_state_is_case_insensitive(self):
        self.assertEqual(job_state(pb.Job(state='SUCCEEDED')), 'succeeded')
        self.assertEqual(job_state(pb.Job(state='')), '')


if __name__ == '__main__':
    unittest.main()
