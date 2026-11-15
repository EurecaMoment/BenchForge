import hashlib
import json
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import get_context
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Lock
import time
import unittest

import grpc
from google.protobuf.empty_pb2 import Empty

from capabilities.isaac_sim.grpc import simulator_pb2 as pb
from capabilities.isaac_sim.grpc.simulator_pb2_grpc import SimulatorServicer, add_SimulatorServicer_to_server
from capabilities.isaac_sim.grpc.tasks import DurableTasks
from capabilities.isaac_sim.grpc.transport import A100Client, RemoteJobFailed
from capabilities.isaac_sim.tool_api import execute
from service_access.request_context import RunCancelled


def download_in_process(config, serialized_job, directory):
    client = A100Client(config)
    try:
        return client.download(pb.Job.FromString(serialized_job), directory)
    finally:
        client.close()


class Simulator(SimulatorServicer):
    """Wire-contract fixture; no renderer or physical evidence is simulated here."""
    def __init__(self):
        self.lock = Lock()
        self.jobs, self.payloads, self.blobs = {}, {}, {}
        self.submissions, self.cancels, self.offsets = 0, 0, []
        self.lose_reply, self.drop_download = False, False
        self.download_gates = {}

    def GetCapabilities(self, request, context):
        return pb.Capabilities(api_version='a100sim.v1', simulator_version='contract-test')

    def SubmitTask(self, request, context):
        with self.lock:
            self.submissions += 1
            encoded = request.SerializeToString(deterministic=True)
            if request.request_id in self.payloads and self.payloads[request.request_id] != encoded:
                context.abort(grpc.StatusCode.ALREADY_EXISTS, 'request_id has another payload')
            self.payloads[request.request_id] = encoded
            identity = 'job-' + request.request_id
            if identity not in self.jobs:
                self.jobs[identity] = pb.Job(job_id=identity, request_id=request.request_id,
                    operation=request.WhichOneof('task'), state=pb.QUEUED, revision=1)
            if self.lose_reply:
                self.lose_reply = False
                context.abort(grpc.StatusCode.UNAVAILABLE, 'reply lost after admission')
            return self.jobs[identity]

    def GetJob(self, request, context):
        if request.job_id not in self.jobs:
            context.abort(grpc.StatusCode.NOT_FOUND, 'job missing')
        return self.jobs[request.job_id]

    def WatchJob(self, request, context):
        while context.is_active():
            value = self.jobs[request.job_id]
            if value.revision > request.after_revision:
                yield value
                return
            time.sleep(.005)

    def CancelJob(self, request, context):
        self.cancels += 1
        job = self.jobs[request.job_id]
        if job.state != pb.QUEUED:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, 'already running')
        job.state, job.revision = pb.CANCELLED, job.revision + 1
        return job

    def DownloadArtifact(self, request, context):
        self.offsets.append(request.offset)
        if request.artifact_id in self.download_gates:
            entered, release = self.download_gates[request.artifact_id]
            entered.set()
            if not release.wait(15):
                context.abort(grpc.StatusCode.DEADLINE_EXCEEDED, 'test download gate timed out')
        data = self.blobs[request.artifact_id]
        end = min(len(data), request.offset + 3)
        yield pb.DownloadChunk(offset=request.offset, data=data[request.offset:end])
        if self.drop_download:
            self.drop_download = False
            context.abort(grpc.StatusCode.UNAVAILABLE, 'connection lost')
        if end < len(data):
            yield pb.DownloadChunk(offset=end, data=data[end:])

    def UploadAsset(self, requests, context):
        chunks = list(requests)
        data = b''.join(row.data for row in chunks)
        digest = hashlib.sha256(data).hexdigest()
        if chunks[0].sha256 != digest:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, 'digest mismatch')
        return pb.AssetFile(asset_id=chunks[0].asset_id or 'asset-test',
            relative_path=chunks[0].relative_path, size_bytes=len(data), sha256=digest)


class GrpcContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.simulator = Simulator()
        self.pool = ThreadPoolExecutor(max_workers=8)
        self.server = grpc.server(self.pool)
        add_SimulatorServicer_to_server(self.simulator, self.server)
        port = self.server.add_insecure_port('127.0.0.1:0')
        self.server.start()
        self.config = {'providers': {'IsaacSim4A100': {
            'grpc': {'host': '127.0.0.1', 'port': port, 'timeout_seconds': 20}}}}
        self.client = A100Client(self.config)

    def tearDown(self):
        self.client.close()
        self.server.stop(0).wait()
        self.pool.shutdown()
        self.temporary.cleanup()

    def requests(self):
        return DurableTasks(self.client, self.root / 'requests', 'scene-test')

    def test_actual_service_name_and_retired_method_rejection(self):
        self.assertEqual(self.client.call('GetCapabilities').api_version, 'a100sim.v1')
        old = self.client.channel.unary_unary('/benchforge.isaac.IsaacSim/JobStatus',
            request_serializer=Empty.SerializeToString)
        with self.assertRaises(grpc.RpcError) as raised:
            old(Empty(), timeout=1)
        self.assertEqual(raised.exception.code(), grpc.StatusCode.UNIMPLEMENTED)

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

    def test_concurrent_same_identity_submits_once_and_other_scenes_do_not_block(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs = list(pool.map(lambda _: self.requests().submit('same', {'snapshot': {}}), range(4)))
        self.assertEqual(len({job.job_id for job in jobs}), 1)
        self.assertEqual(self.simulator.submissions, 1)
        other = DurableTasks(self.client, self.root / 'other', 'other-scene')
        self.assertNotEqual(other.submit('same', {'snapshot': {}}).job_id, jobs[0].job_id)

    def test_wait_observes_terminal_failure_and_never_claims_success(self):
        job = self.requests().submit('failed', {'snapshot': {}})
        self.simulator.jobs[job.job_id].state = pb.INTERRUPTED
        self.simulator.jobs[job.job_id].revision += 1
        with self.assertRaises(RemoteJobFailed) as raised:
            self.client.wait(job, timeout=.5)
        self.assertEqual(raised.exception.job['state'], 'INTERRUPTED')

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

    def test_watch_budget_does_not_submit_or_cancel(self):
        job = self.requests().submit('waiting', {'snapshot': {}})
        with self.assertRaises(TimeoutError):
            self.client.wait(job, timeout=.03)
        self.assertEqual((self.simulator.submissions, self.simulator.cancels), (1, 0))

    def test_manifest_download_resumes_and_preserves_equal_basenames(self):
        self.simulator.blobs = {'first': b'abcdef', 'second': b'ghijkl'}
        job = pb.Job(job_id='download', state=pb.SUCCEEDED, artifacts=[
            pb.Artifact(artifact_id=key, name='camera.png', size_bytes=len(data))
            for key, data in self.simulator.blobs.items()])
        self.simulator.drop_download = True
        with self.assertRaises(grpc.RpcError):
            self.client.download(job, self.root / 'downloads')
        files = self.client.download(job, self.root / 'downloads')
        self.assertIn(3, self.simulator.offsets)
        self.assertEqual([Path(row['source_path']).read_bytes() for row in files], [b'abcdef', b'ghijkl'])
        self.assertEqual(len({row['source_path'] for row in files}), 2)
        self.assertEqual(files[0]['sha256'], hashlib.sha256(b'abcdef').hexdigest())

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

    def test_native_operation_sets_stage_in_download_artifact_contract(self):
        self.simulator.blobs['dataset'] = b'collected data'
        for index, (operation, stage) in enumerate([
            ('extension.world.collect', 'S3'), ('extension.scene.capture', 'S2'),
            ('extension.scene.export', 'S2'), ('extension.other', None),
        ]):
            with self.subTest(operation=operation):
                identity = f'download-{index}'
                self.simulator.jobs[identity] = pb.Job(job_id=identity, operation=operation,
                    state=pb.SUCCEEDED, artifacts=[pb.Artifact(artifact_id='dataset', name='dataset.zip',
                                                              size_bytes=len(self.simulator.blobs['dataset']))])
                result = execute({'action': 'download', 'job_id': identity},
                                 self.root / 'operations' / identity, self.config)
                artifact = result['artifacts'][0]
                self.assertEqual(artifact.get('stage'), stage)
                self.assertEqual(artifact['path'], result['files'][0]['source_path'])
                self.assertEqual(Path(artifact['path']).read_bytes(), self.simulator.blobs['dataset'])
                if stage is None:
                    self.assertNotIn('stage', artifact)

    def test_streaming_upload_checks_source_digest(self):
        source = self.root / 'scene.usda'
        source.write_bytes(b'x' * (256 * 1024 + 7))
        result = self.client.upload(source)
        self.assertEqual(result['sha256'], hashlib.sha256(source.read_bytes()).hexdigest())
        self.assertEqual(int(result['size_bytes']), source.stat().st_size)


if __name__ == '__main__':
    unittest.main()
