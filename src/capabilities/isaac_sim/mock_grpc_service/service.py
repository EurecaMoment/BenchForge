"""In-memory isaacsim_rpc.v1 SimulationService for tests and local runs.

The mock owns no renderer. It records submissions, produces small synthetic
artifacts (RGB/depth/semantic/labels) with real sha256 digests, streams them
with byte offsets, and exposes the same terminal states as the wire contract.

Run it with the bundled scripts (start/shutdown/restart) or directly:

    PYTHONPATH=src .venv/bin/python -m capabilities.isaac_sim.mock_grpc_service --port 18500

The default endpoint matches `providers.IsaacSim4A100.grpc` in
`config/benchforge.local.json`.
"""
from __future__ import annotations

import argparse
from concurrent import futures
import hashlib
import json
from pathlib import Path
import signal
import tempfile
from threading import RLock
import time

import grpc
import numpy as np
from PIL import Image

from capabilities.isaac_sim.grpc import simulation_pb2 as pb
from capabilities.isaac_sim.grpc import simulation_pb2_grpc as pb_grpc


PROTOCOL = 'isaacsim_rpc.v1'


# 构造测试用服务配置，端点默认指向本地 mock 端口。
def simulation_config(port=18500, *, chunk_bytes=1024, timeout_seconds=5, mesh_assets=None):
    return {'providers': {'IsaacSim4A100': {
        'grpc': {'host': '127.0.0.1', 'port': port, 'timeout_seconds': timeout_seconds},
        'capture_timeout_seconds': 30,
        'simulation': {'steps': 180, 'dt': 1 / 60, 'gravity': 9.81, 'ground': True,
                       'capture_target_frames': 24, 'capture_every_steps': None},
        'render': {'image_size': 64, 'samples_per_pixel': 8, 'fov_degrees': 50, 'seed': 0},
        'artifacts': {'chunk_bytes': chunk_bytes, 'concurrency': 1},
        'mesh_assets': dict(mesh_assets or {}),
    }}}


# 生成可辨识的确定性像素，保证质量评估有足够对比度。
def _pixels(size, seed):
    rng = np.random.default_rng(seed)
    return rng.integers(24, 232, size=(size, size, 3), dtype=np.uint8)


class MockSimulationService(pb_grpc.SimulationServiceServicer):
    """Thread-safe in-memory service used by transport and capture tests."""

    # 初始化任务表、请求幂等表、产物字节与故障注入开关。
    def __init__(self):
        self.lock = RLock()
        self.root = Path(tempfile.mkdtemp(prefix='isaacsim-mock-'))
        self.jobs = {}
        self.request_ids = {}
        self.request_payloads = {}
        self.job_sequences = {}
        self.blobs = {}
        self.acked = set()
        self.submissions = 0
        self.download_calls = []
        self.frames = []
        self.lose_reply = False
        self.drop_download = False
        self.drop_after_chunks = 1
        self.submit_state = 'succeeded'
        self.status_sequence = []
        self.error_text = ''
        self.status = 'ready'
        self.api_version = PROTOCOL

    # 清理临时产物目录。
    def cleanup(self):
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    # 在复用同一服务进程时重置全部状态，避免用例之间互相污染。
    def reset(self):
        with self.lock:
            self.jobs.clear()
            self.request_ids.clear()
            self.request_payloads.clear()
            self.job_sequences.clear()
            self.blobs.clear()
            self.acked.clear()
            self.submissions = 0
            self.download_calls = []
            self.frames = []
            self.lose_reply = False
            self.drop_download = False
            self.drop_after_chunks = 1
            self.submit_state = 'succeeded'
            self.status_sequence = []
            self.error_text = ''
            self.status = 'ready'
            self.api_version = PROTOCOL

    # 返回健康状态与契约版本，供静态/实时预检读取。
    def Health(self, request, context):
        return pb.HealthResponse(status=self.status, engine='mock', api_version=self.api_version,
                                 renderer='MockRTX', render_backend='mock', native_rtx=False,
                                 artifacts_free_bytes=1 << 30, retention_seconds=3600,
                                 supported_shapes=['cube', 'sphere', 'mesh'])

    # 幂等提交：同一 request_id 与载荷返回原任务；载荷变化明确冲突。
    def SubmitJob(self, request, context):
        payload = request.simulation.SerializeToString(deterministic=True)
        with self.lock:
            self.submissions += 1
            if request.request_id in self.request_payloads:
                if self.request_payloads[request.request_id] != payload:
                    context.abort(grpc.StatusCode.ALREADY_EXISTS,
                                  'request_id already used with another payload')
                job_id = self.request_ids[request.request_id]
                return pb.JobHandle(id=job_id, state=self.jobs[job_id]['job'].state)
            job_id = hashlib.sha256((request.request_id + str(time.time())).encode()).hexdigest()[:32]
            self.request_payloads[request.request_id] = payload
            self.request_ids[request.request_id] = job_id
            job = self._build_job(job_id, request.simulation)
            self.jobs[job_id] = {'job': job, 'request_id': request.request_id}
            if self.status_sequence:
                self.job_sequences[job_id] = list(self.status_sequence)
            if self.lose_reply:
                self.lose_reply = False
                # 任务已受理但应答丢失；客户端必须用同一 identity 重试。
                context.abort(grpc.StatusCode.UNAVAILABLE, 'reply lost after admission')
            return pb.JobHandle(id=job_id, state=job.state)

    # 读取任务最新状态；可按预设状态序列推进以便测试轮询。
    def GetJob(self, request, context):
        with self.lock:
            record = self.jobs.get(request.job_id)
            if record is None:
                context.abort(grpc.StatusCode.NOT_FOUND, 'unknown job')
            job = pb.Job()
            job.CopyFrom(record['job'])
            sequence = self.job_sequences.get(request.job_id)
            if sequence:
                job.state = sequence.pop(0)
                if sequence and job.state == 'running':
                    job.phase = 'simulating'
            return job

    # 把取消请求写入任务状态并返回最新回执。
    def CancelJob(self, request, context):
        with self.lock:
            record = self.jobs.get(request.job_id)
            if record is None:
                context.abort(grpc.StatusCode.NOT_FOUND, 'unknown job')
            job = pb.Job()
            job.CopyFrom(record['job'])
            if job.state not in ('succeeded', 'failed', 'interrupted'):
                job.state = 'cancelled'
                record['job'].state = 'cancelled'
            return job

    # 确认产物已被本地校验，返回释放的字节数。
    def AckJob(self, request, context):
        with self.lock:
            record = self.jobs.get(request.job_id)
            if record is None:
                context.abort(grpc.StatusCode.NOT_FOUND, 'unknown job')
            self.acked.add(request.job_id)
            freed = sum(item.size_bytes for item in record['job'].artifacts)
            return pb.AckJobResponse(released=True, freed_bytes=freed)

    # 可选的服务端推送；立即返回当前状态，避免测试阻塞。
    def WatchJob(self, request, context):
        yield self.GetJob(request, context)

    # 按 offset 流式返回产物，支持故障注入与偏移记录。
    def DownloadArtifact(self, request, context):
        with self.lock:
            self.download_calls.append((request.job_id, request.artifact_id, request.offset))
            blob = self.blobs.get(request.artifact_id)
            chunk = request.chunk_bytes or 1 << 20
            offset = request.offset
            dropped = self.drop_download
        if blob is None:
            context.abort(grpc.StatusCode.NOT_FOUND, 'unknown artifact')
        if offset > len(blob):
            context.abort(grpc.StatusCode.OUT_OF_RANGE, 'offset beyond artifact')
        sent = 0
        while offset < len(blob):
            block = blob[offset:offset + chunk]
            if dropped and sent >= self.drop_after_chunks:
                context.abort(grpc.StatusCode.UNAVAILABLE, 'stream interrupted')
            yield pb.DownloadChunk(offset=offset, data=block)
            offset += len(block)
            sent += 1

    # 依据请求的 steps/capture 生成最终刚体状态与逐帧产物。
    def _build_job(self, job_id, simulation):
        steps = simulation.steps or 180
        frames = self._frame_steps(simulation, steps)
        size = simulation.render.size or 64
        bodies = [pb.BodyState(
            id=body.id,
            prim_path='/World/Objects/' + body.id,
            pose_xyzw=[body.position.x, body.position.y, body.position.z, 0.0, 0.0, 0.0, 1.0],
            velocity=[0.0] * 6) for body in simulation.bodies]
        job = pb.Job(id=job_id, state=self.submit_state, phase='writing_artifacts', steps=steps,
                     dt=simulation.dt or 1 / 60, rigid_bodies=len(bodies), bodies=bodies,
                     isaac_sim='mock', physics_device='cpu', native_rtx=False,
                     error=self.error_text,
                     frames_done=len(frames), frames_total=len(frames))
        job.render.renderer = 'MockRTX'
        job.render.backend = 'mock'
        job.render.size = size
        for step in frames:
            artifacts, paths = self._write_frame(job_id, step, size, bodies)
            job.artifacts.extend(artifacts)
            job.outputs.frames.append(pb.FrameArtifact(
                frame=step, image=paths['image'], depth_m=paths['depth'],
                semantic_ids=paths['semantic'], semantic_labels=paths['labels']))
            self.frames.append(step)
        return job

    # 合并显式帧、every 间隔与最终帧，得到去重排序的采集步。
    def _frame_steps(self, simulation, steps):
        frames = list(simulation.capture.frames)
        if not frames:
            every = simulation.capture.every if simulation.HasField('capture') and \
                simulation.capture.HasField('every') else None
            if every:
                frames = list(range(every, steps + 1, every))
            frames.append(steps)
        return sorted(set(frames))

    # 写入单帧 RGB/深度/语义/标签并登记摘要，返回清单与绝对路径。
    def _write_frame(self, job_id, step, size, bodies):
        directory = self.root / job_id / 'frames' / f'{step:06d}'
        directory.mkdir(parents=True, exist_ok=True)
        image = np.asarray(Image.fromarray(_pixels(size, step)))
        semantic = np.zeros((size, size), dtype=np.uint32)
        depth = np.full((size, size), 2.0, dtype=np.float32)
        instances = {}
        for index, body in enumerate(bodies):
            identity = index + 1
            row, column = divmod(index, 4)
            top, left = 4 + row * (size // 4), 4 + column * (size // 4)
            region = (slice(top, min(top + size // 5, size)), slice(left, min(left + size // 5, size)))
            semantic[region] = identity
            depth[region] = 1.5
            instances[str(identity)] = {'prim_path': '/World/Objects/' + body.id}
        labels = {'classes': {str(key): value['prim_path'].rsplit('/', 1)[-1]
                              for key, value in instances.items()},
                  'instances': instances, 'depth_convention': 'optical_axis_z_m',
                  'backend': 'mock'}
        files = {'image': 'image.png', 'depth': 'depth.npy',
                 'semantic': 'semantic.npy', 'labels': 'labels.json'}
        paths, artifacts = {}, []
        for key, name in files.items():
            path = directory / name
            if key == 'image':
                Image.fromarray(image).save(path)
            elif key == 'depth':
                np.save(path, depth, allow_pickle=False)
            elif key == 'semantic':
                np.save(path, semantic, allow_pickle=False)
            else:
                path.write_bytes(json.dumps(labels).encode())
            blob = path.read_bytes()
            artifact_id = f'{step:06d}-{key}'
            self.blobs[artifact_id] = blob
            artifacts.append(pb.Artifact(
                artifact_id=artifact_id, name=name,
                relative_path=f'frames/{step:06d}/{name}', size_bytes=len(blob),
                sha256=hashlib.sha256(blob).hexdigest(),
                media_type='image/png' if key == 'image' else 'application/octet-stream'))
            paths[key] = str(path)
        return artifacts, paths


# 启动 mock 服务；优先使用指定端口，被占用时回退到临时端口。
def serve_mock(servicer=None, port=18500, *, fallback=True, max_workers=8):
    servicer = servicer or MockSimulationService()
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    pb_grpc.add_SimulationServiceServicer_to_server(servicer, server)
    bound = server.add_insecure_port(f'127.0.0.1:{port}')
    if not bound and fallback:
        bound = server.add_insecure_port('127.0.0.1:0')
    if not bound:
        raise RuntimeError(f'cannot bind mock simulation service on 127.0.0.1:{port}')
    server.start()
    return server, bound, servicer


# 命令行入口：前台运行，Ctrl-C 或 SIGTERM 时停止服务并清理临时产物。
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18500)
    arguments = parser.parse_args()
    server, port, servicer = serve_mock(port=arguments.port, fallback=False)
    print(f'isaacsim_rpc.v1 mock listening on 127.0.0.1:{port}', flush=True)

    # 让管理脚本的 SIGTERM 走与 Ctrl-C 相同的清理路径。
    def terminate(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, terminate)
    try:
        server.wait_for_termination()
    except KeyboardInterrupt:
        pass
    finally:
        server.stop(0)
        servicer.cleanup()


if __name__ == '__main__':
    main()
