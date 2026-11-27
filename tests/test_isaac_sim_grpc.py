import hashlib
import json
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
import time
import unittest
from unittest.mock import patch

from tests.database_fixtures import use_postgres

import grpc
from google.protobuf.empty_pb2 import Empty

from capabilities.isaac_sim.grpc import native_pb2 as pb, simulation_pb2 as rpc
from capabilities.isaac_sim.grpc.simulation_pb2_grpc import (
    SimulationServiceServicer, add_SimulationServiceServicer_to_server,
)
from capabilities.isaac_sim.grpc.tasks import DurableTasks
from capabilities.isaac_sim.grpc.transport import A100Client, RemoteJobFailed
from capabilities.isaac_sim.tool_api import execute
from service_access.request_context import RunCancelled


# 在独立进程创建客户端执行下载，并确保关闭连接，供跨进程回执测试。
def download_in_process(config, serialized_job, directory):
    client = A100Client(config)
    try:
        return client.download(pb.Job.FromString(serialized_job), directory)
    finally:
        client.close()


class Simulator(SimulationServiceServicer):
    """Wire-contract fixture; no renderer or physical evidence is simulated here."""
    # 初始化线程安全的假仿真服务、任务和文件存储，以及故障注入开关。
    def __init__(self):
        self.lock = Lock()
        self.jobs, self.payloads, self.blobs = {}, {}, {}
        self.submissions, self.cancels, self.offsets = 0, 0, []
        self.lose_reply, self.drop_download = False, False
        self.download_gates = {}
        self.metadata = {}

    # 返回测试用的真实协议版本能力回执。
    def GetCapabilities(self, request, context):
        return pb.Capabilities(api_version='isaacsim_rpc.v1', simulator_version='contract-test')

    # 模拟按请求编号幂等提交，并可注入提交成功但响应丢失的情况。
    def SubmitJob(self, request, context):
        self.metadata = dict(context.invocation_metadata())
        with self.lock:
            self.submissions += 1
            encoded = request.SerializeToString(deterministic=True)
            if request.request_id in self.payloads and self.payloads[request.request_id] != encoded:
                context.abort(grpc.StatusCode.ALREADY_EXISTS, 'request_id has another payload')
            self.payloads[request.request_id] = encoded
            identity = 'job-' + request.request_id
            if identity not in self.jobs:
                self.jobs[identity] = pb.Job(job_id=identity, request_id=request.request_id,
                    operation=(request.native_feature.feature_id if request.WhichOneof('workload') == 'native_feature'
                               else request.native_task.WhichOneof('task')), state=pb.QUEUED, revision=1)
            if self.lose_reply:
                self.lose_reply = False
                context.abort(grpc.StatusCode.UNAVAILABLE, 'reply lost after admission')
            return rpc.JobHandle(id=identity, state='queued')

    # 按任务编号返回状态，未知任务按 gRPC 未找到错误终止。
    def GetJob(self, request, context):
        if request.job_id not in self.jobs:
            context.abort(grpc.StatusCode.NOT_FOUND, 'job missing')
        return rpc.Job(id=request.job_id, native_job=self.jobs[request.job_id])

    # 等待任务修订超过游标后发送更新，支持客户端取消观测。
    def WatchJob(self, request, context):
        while context.is_active():
            value = self.jobs[request.job_id]
            if value.revision > request.after_revision:
                yield rpc.Job(id=value.job_id, native_job=value)
                return
            time.sleep(.005)

    # 仅允许取消排队任务，记录取消次数并返回更新后的状态。
    def CancelJob(self, request, context):
        self.cancels += 1
        job = self.jobs[request.job_id]
        if job.state != pb.QUEUED:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, 'already running')
        job.state, job.revision = pb.CANCELLED, job.revision + 1
        return rpc.Job(id=job.job_id, native_job=job)

    # 按偏移量流式返回测试文件，可注入阻塞或中断以验证恢复行为。
    def DownloadArtifact(self, request, context):
        self.offsets.append(request.offset)
        if request.artifact_id in self.download_gates:
            entered, release = self.download_gates[request.artifact_id]
            entered.set()
            if not release.wait(15):
                context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, 'test download gate timed out')
        data = self.blobs[request.artifact_id]
        end = min(len(data), request.offset + 3)
        yield rpc.DownloadChunk(offset=request.offset, data=data[request.offset:end])
        if self.drop_download:
            self.drop_download = False
            context.abort(grpc.StatusCode.UNAVAILABLE, 'connection lost')
        if end < len(data):
            yield rpc.DownloadChunk(offset=end, data=data[end:])

    # 合并上传块并核验声明摘要，返回测试资源身份。
    def UploadAsset(self, requests, context):
        chunks = list(requests)
        data = b''.join(row.data for row in chunks)
        digest = hashlib.sha256(data).hexdigest()
        if chunks[0].sha256 != digest:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, 'digest mismatch')
        return pb.AssetFile(asset_id=chunks[0].asset_id or 'asset-test',
            relative_path=chunks[0].relative_path, size_bytes=len(data), sha256=digest)


class GrpcContractTests(unittest.TestCase):
    # 启动随机端口的本地 gRPC 服务及客户端，隔离远端任务契约测试。
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.simulator = Simulator()
        self.pool = ThreadPoolExecutor(max_workers=8)
        self.server = grpc.server(self.pool)
        add_SimulationServiceServicer_to_server(self.simulator, self.server)
        port = self.server.add_insecure_port('127.0.0.1:0')
        self.server.start()
        self.config = {'providers': {'IsaacSim4A100': {
            'grpc': {'host': '127.0.0.1', 'port': port, 'timeout_seconds': 20}}}}
        self.client = A100Client(self.config)

    # 关闭客户端与 gRPC 服务，等待线程池结束并删除临时文件。
    def tearDown(self):
        self.client.close()
        self.server.stop(0).wait()
        self.pool.shutdown()
        self.temporary.cleanup()

    # 为同一场景建立共享回执目录的持久任务客户端。
    def requests(self):
        return DurableTasks(self.client, self.root / 'requests', 'scene-test')

    # 验证客户端使用当前真实服务名称，退役方法不会被错误兼容。
    def test_actual_service_name_and_retired_method_rejection(self):
        self.assertEqual(self.client.call('GetCapabilities').api_version, 'isaacsim_rpc.v1')
        old = self.client.channel.unary_unary('/a100sim.v1.Simulator/GetCapabilities',
            request_serializer=Empty.SerializeToString)
        with self.assertRaises(grpc.RpcError) as raised:
            old(Empty(), timeout=1)
        self.assertEqual(raised.exception.code(), grpc.StatusCode.UNIMPLEMENTED)

    def test_feature_uses_unified_submission_and_configured_auth(self):
        connection = self.config['providers']['IsaacSim4A100']['grpc']
        with patch.dict(os.environ, {'TEST_ISAAC_TOKEN': 'transport-test'}), patch.dict(
                connection, {'token_env': 'TEST_ISAAC_TOKEN'}):
            client = A100Client(self.config)
            self.addCleanup(client.close)
            job = client.submit({'feature_id': 'sessions.inspect', 'arguments': {'limit': 2}},
                                'retained-feature', feature=True)
        self.assertEqual(job.operation, 'sessions.inspect')
        self.assertEqual(self.simulator.metadata['authorization'], 'Bearer transport-test')
        saved = rpc.SubmitJobRequest.FromString(self.simulator.payloads[job.request_id])
        self.assertEqual(saved.WhichOneof('workload'), 'native_feature')
        self.assertEqual(dict(saved.native_feature.arguments), {'limit': 2})

    def test_pre_migration_frozen_request_resumes_same_native_job(self):
        requests = self.requests()
        job = requests.submit('existing', {'snapshot': {}})
        file = requests.directory / 'existing' / 'request.json'
        frozen = json.loads(file.read_text())
        frozen['protocol'] = 'a100sim.v1.Simulator'
        file.write_text(json.dumps(frozen))
        self.assertEqual(requests.submit('existing', {'snapshot': {}}).job_id, job.job_id)
        self.assertEqual(self.simulator.submissions, 1)
        self.assertEqual(json.loads(file.read_text()), frozen)

    # 验证提交响应丢失及客户端重启后仍恢复原任务而非新建任务。
    def test_lost_submit_reply_and_restart_keep_the_original_job(self):
        self.simulator.lose_reply = True
        job = self.requests().submit('import', {'snapshot': {}})
        self.assertEqual(self.simulator.submissions, 2)
        self.assertEqual(len(self.simulator.jobs), 1)
        restored = self.requests().submit('import', {'snapshot': {}})
        self.assertEqual(restored.job_id, job.job_id)
        self.assertEqual(self.simulator.submissions, 2)
        with self.assertRaisesRegex(ValueError, 'request changed'):
            self.requests().submit('import', {'step': {'steps': 2}})

    # 验证同身份并发请求只创建一次，其他场景不被该请求锁阻塞。
    def test_concurrent_same_identity_submits_once_and_other_scenes_do_not_block(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = list(pool.map(lambda _: self.requests().submit('same', {'snapshot': {}}), range(4)))
        self.assertEqual(len({job.job_id for job in jobs}), 1)
        self.assertEqual(self.simulator.submissions, 1)
        other = DurableTasks(self.client, self.root / 'other', 'other-scene')
        self.assertNotEqual(other.submit('same', {'snapshot': {}}).job_id, jobs[0].job_id)

    # 验证等待接口将远端终态失败报告为错误，不伪称成功。
    def test_wait_observes_terminal_failure_and_never_claims_success(self):
        job = self.requests().submit('failed', {'snapshot': {}})
        self.simulator.jobs[job.job_id].state = pb.INTERRUPTED
        self.simulator.jobs[job.job_id].revision += 1
        with self.assertRaises(RemoteJobFailed) as raised:
            self.client.wait(job, timeout=.5)
        self.assertEqual(raised.exception.job['state'], 'INTERRUPTED')

    # 验证本地停止仅脱离等待，显式取消则遵从服务端任务状态限制。
    def test_shutdown_detaches_and_explicit_cancellation_obeys_server(self):
        job = self.requests().submit('queued', {'snapshot': {}})
        stop = Event()
        stop.set()
        with self.assertRaises(RunCancelled):
            self.client.wait(job, stop=stop)
        self.assertEqual(self.simulator.cancels, 0)
        stop.canceled = True
        with self.assertRaises(RunCancelled):
            self.client.wait(job, stop=stop)
        self.assertEqual(self.simulator.jobs[job.job_id].state, pb.CANCELLED)
        running = self.requests().submit('running', {'snapshot': {}})
        self.simulator.jobs[running.job_id].state = pb.RUNNING
        with self.assertRaises(grpc.RpcError) as raised:
            self.client.cancel(running.job_id)
        self.assertEqual(raised.exception.code(), grpc.StatusCode.FAILED_PRECONDITION)
        self.assertEqual(self.simulator.jobs[running.job_id].state, pb.RUNNING)

    # 验证观测超时既不重新提交，也不擅自取消远端任务。
    def test_watch_budget_does_not_submit_or_cancel(self):
        job = self.requests().submit('waiting', {'snapshot': {}})
        with self.assertRaises(TimeoutError):
            self.client.wait(job, timeout=.03)
        self.assertEqual((self.simulator.submissions, self.simulator.cancels), (1, 0))

    # 验证按清单断点续传，并保留不同目录下相同文件名的产物。
    def test_manifest_download_resumes_and_preserves_equal_basenames(self):
        self.simulator.blobs = {'first': b'abcdef', 'second': b'ghijkl'}
        job = pb.Job(job_id='download', state=pb.SUCCEEDED, artifacts=[
            pb.Artifact(artifact_id=key, name='camera.png', size_bytes=len(data))
            for key, data in self.simulator.blobs.items()])
        self.simulator.drop_download = True
        files = self.client.download(job, self.root / 'downloads')
        self.assertIn(3, self.simulator.offsets)
        self.assertEqual([Path(row['source_path']).read_bytes() for row in files], [b'abcdef', b'ghijkl'])
        self.assertEqual(len({row['source_path'] for row in files}), 2)
        self.assertEqual(files[0]['sha256'], hashlib.sha256(b'abcdef').hexdigest())

    # 验证清单中的越界路径及恢复时变化的清单均被拒绝。
    def test_manifest_traversal_and_changed_manifest_are_rejected(self):
        job = pb.Job(job_id='bad', state=pb.SUCCEEDED, artifacts=[
            pb.Artifact(artifact_id='identity', name='../outside', size_bytes=1)])
        with self.assertRaises(ValueError):
            self.client.download(job, self.root / 'bad')
        self.assertFalse((self.root / 'outside').exists())
        job.artifacts[0].name = 'changed'
        with self.assertRaisesRegex(ValueError, 'manifest changed'):
            self.client.download(job, self.root / 'bad')
        with self.assertRaises(ValueError):
            self.requests().submit('..', {'snapshot': {}})

    # 验证跨进程下载共用单一回执，同步不阻塞其他任务。
    def test_concurrent_download_processes_share_one_receipt_without_blocking_other_jobs(self):
        self.simulator.blobs = {'shared': b'abcdef', 'independent': b'ghijkl'}
        entered, release = Event(), Event()
        self.simulator.download_gates['shared'] = (entered, release)
        jobs = {key: pb.Job(job_id=key, state=pb.SUCCEEDED, artifacts=[
            pb.Artifact(artifact_id=key, name='dataset.zip', size_bytes=len(data))])
            for key, data in self.simulator.blobs.items()}
        with ProcessPoolExecutor(max_workers=4, mp_context=get_context('spawn')) as pool:
            shared = [pool.submit(download_in_process, self.config, jobs['shared'].SerializeToString(),
                                  self.root / 'shared') for _ in range(3)]
            try:
                self.assertTrue(entered.wait(10), 'Shared download never started')
                other = pool.submit(download_in_process, self.config, jobs['independent'].SerializeToString(),
                                    self.root / 'independent').result(timeout=10)
                self.assertEqual(Path(other[0]['source_path']).read_bytes(), b'ghijkl')
            finally:
                release.set()
            results = [future.result(timeout=10) for future in shared]
        self.assertTrue(all(result == results[0] for result in results))
        self.assertEqual(Path(results[0][0]['source_path']).read_bytes(), b'abcdef')
        self.assertEqual(self.simulator.offsets, [0, 0])
        self.assertEqual(list(self.root.rglob('*.partial')), [])

    # 验证原生操作下载回执包含其真实生产阶段身份。
    def test_native_operation_sets_stage_in_download_artifact_contract(self):
        use_postgres(self)
        self.simulator.blobs['dataset'] = b'collected data'
        for index, (operation, stage) in enumerate([
            ('extension.world.collect', 'S3'), ('extension.scene.capture', 'S2'),
            ('extension.scene.export', 'S2'), ('extension.extended.world.collect', 'S3'),
            ('extension.extended.scene.capture', 'S2'), ('extension.extended.scene.export', 'S2'),
            ('extension.other', None), ('extension.extended.other', None),
        ]):
            with self.subTest(operation=operation):
                identity = f'download-{index}'
                self.simulator.jobs[identity] = pb.Job(job_id=identity, operation=operation,
                    state=pb.SUCCEEDED, artifacts=[pb.Artifact(artifact_id='dataset', name='dataset.zip',
                                                              size_bytes=len(self.simulator.blobs['dataset']))])
                from storage.native_jobs import NativeJobs
                NativeJobs(self.root).remember(self.client.endpoint, self.simulator.jobs[identity], 'local-cli')
                result = execute({'action': 'download', 'job_id': identity},
                                 self.root / 'operations' / identity, self.config)
                artifact = result['artifacts'][0]
                self.assertEqual(artifact.get('stage'), stage)
                self.assertEqual(artifact['path'], result['files'][0]['source_path'])
                self.assertEqual(Path(artifact['path']).read_bytes(), self.simulator.blobs['dataset'])
                if stage is None:
                    self.assertNotIn('stage', artifact)

    # 验证流式上传保持完整字节并核对源文件摘要。
    def test_streaming_upload_checks_source_digest(self):
        source = self.root / 'scene.usda'
        source.write_bytes(b'x' * (256 * 1024 + 7))
        result = self.client.upload(source)
        self.assertEqual(result['sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(int(result['size_bytes']), source.stat().st_size)

    # 验证工具在调用 RPC 前就限定远端任务的会话与端点归属。
    def test_tools_scope_native_jobs_to_session_and_endpoint_before_rpc(self):
        use_postgres(self)
        owner = {'session_id': 'session-owner', 'call_id': 'call-one'}
        request = {'action':'submit', 'request_id':'same-key', 'request':{'snapshot':{}}, '_dsh_context':owner}
        first = execute(request, self.root / 'operations' / 'one', self.config)
        repeated = execute(request, self.root / 'operations' / 'again', self.config)
        self.assertEqual(first['job']['job_id'], repeated['job']['job_id'])
        other = {'session_id':'session-other', 'call_id':'call-two'}
        second = execute({**request, '_dsh_context':other}, self.root / 'operations' / 'two', self.config)
        self.assertNotEqual(first['job']['job_id'], second['job']['job_id'])
        for action in ('status', 'wait', 'cancel', 'download'):
            with self.subTest(action=action), self.assertRaises(PermissionError):
                execute({'action':action,'job_id':first['job']['job_id'],'_dsh_context':other},
                        self.root / 'operations' / action, self.config)
        self.assertEqual(self.simulator.cancels, 0)
        status = execute({'action':'status','job_id':first['job']['job_id'],'_dsh_context':owner},
                         self.root / 'operations' / 'status-owner', self.config)
        self.assertEqual(status['job']['job_id'], first['job']['job_id'])

    # 验证旧提交在回执可核实后复用已有远端任务。
    def test_legacy_submit_reuses_verified_receipt_without_another_submission(self):
        use_postgres(self)
        from storage.native_jobs import NativeJobs
        key, payload = 'old-key', {'snapshot': {}}
        requests = DurableTasks(self.client, self.root / '.benchforge/isaac_sim', str(self.root))
        old = requests.submit(hashlib.sha256(key.encode()).hexdigest(), payload)
        NativeJobs(self.root).remember(self.client.endpoint, old, 'session-owner')
        arguments = {'action': 'submit', 'request_id': key, 'request': payload,
                     '_dsh_context': {'session_id': 'session-owner', 'call_id': 'call-retry'}}
        repeated = execute(arguments, self.root / 'operations/retry', self.config)
        self.assertEqual(repeated['job']['job_id'], old.job_id)
        self.assertEqual(self.simulator.submissions, 1)
        with self.assertRaisesRegex(ValueError, 'request changed'):
            execute({**arguments, 'request': {'snapshot': {'size': 64}}},
                    self.root / 'operations/changed', self.config)

    # 验证旧提交归属未能确认时拒绝新建重复任务。
    def test_unresolved_legacy_submission_does_not_create_another_job(self):
        use_postgres(self)
        key = 'lost-legacy-reply'
        folder = self.root / '.benchforge/isaac_sim' / hashlib.sha256(key.encode()).hexdigest()
        folder.mkdir(parents=True)
        (folder / 'request.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'no receipt'):
            execute({'action': 'submit', 'request_id': key, 'request': {'snapshot': {}}},
                    self.root / 'operations/retry', self.config)
        self.assertEqual(self.simulator.submissions, 0)


if __name__ == '__main__':
    unittest.main()
