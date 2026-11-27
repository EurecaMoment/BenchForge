"""The isaacsim_rpc.v1 SimulationService protocol.

No simulator runtime or shared filesystem is required. Jobs run on the remote
service reached through the configured endpoint (an SSH tunnel in production);
artifacts are streamed back, verified and published locally.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time

import grpc
from google.protobuf.empty_pb2 import Empty
from google.protobuf.json_format import MessageToDict, ParseDict

from capabilities.isaac_sim.grpc import simulation_pb2 as pb
from capabilities.isaac_sim.grpc.receipts import receipt_lock
from capabilities.isaac_sim.grpc.simulation_pb2_grpc import SimulationServiceStub
from service_access.configuration import isaac_config, isaac_endpoint, isaac_unified_settings
from service_access.request_context import RunCancelled
from storage.files import safe_path, write_json


PROTOCOL = 'isaacsim_rpc.v1.SimulationService'
TERMINAL = {'succeeded', 'failed', 'cancelled', 'interrupted'}
TRANSIENT = {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED,
             grpc.StatusCode.RESOURCE_EXHAUSTED}


# 保留协议字段名与默认值转换为 JSON，并将负零规范化以兼容宿主。
def document(message):
    """Preserve protocol names and defaults; DSH JSON also requires positive zero."""
    # 递归清理字典和数组，把浮点零统一为正零。
    def clean(value):
        if isinstance(value, dict):
            return {key: clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return 0 if isinstance(value, float) and value == 0 else value
    return clean(MessageToDict(message, preserving_proto_field_name=True,
                              always_print_fields_with_no_presence=True))


# 状态字符串大小写不敏感，兼容上游枚举输出。
def job_state(job):
    return (job.state or '').strip().lower()


class RemoteJobFailed(RuntimeError):
    # 保留远程任务完整回执，并将状态与错误写入异常。
    def __init__(self, job):
        self.job = document(job)
        super().__init__(f"Isaac Sim job {job.id} {job_state(job)}: {job.error}")


# 自适应轮询间隔：先密后疏，避免长任务持续高频查询。
def poll_interval(elapsed):
    if elapsed < 10:
        return .5
    if elapsed < 60:
        return 2.
    return 5.


class SimulationClient:
    """A shared thread-safe channel; each independent scene owns its own request IDs."""

    # 从服务配置建立带认证与可选 TLS 的 gRPC 连接，设置请求和任务超时。
    def __init__(self, config):
        settings = isaac_unified_settings(isaac_config(config))
        connection = settings.get('grpc', {})
        self.endpoint = isaac_endpoint(connection)
        self.timeout = connection.get('timeout_seconds', 60)
        self.job_timeout = settings.get('capture_timeout_seconds', 1800)
        artifacts = settings.get('artifacts', {})
        self.chunk_bytes = int(artifacts.get('chunk_bytes', 2 * 1024 * 1024))
        token_name = connection.get('token_env')
        token = os.environ.get(token_name, '') if token_name else ''
        if token_name and not token:
            raise ValueError(f'Isaac Sim token environment variable is unset: {token_name}')
        self.metadata = (('authorization', 'Bearer ' + token),) if token else ()
        certificate = connection.get('tls_ca_file')
        options = [('grpc.max_receive_message_length', 64 * 1024 * 1024),
                   ('grpc.enable_http_proxy', 0),
                   ('grpc.keepalive_time_ms', 30000),
                   ('grpc.keepalive_timeout_ms', 10000),
                   ('grpc.keepalive_permit_without_calls', 1)]
        if certificate:
            credentials = grpc.ssl_channel_credentials(Path(certificate).read_bytes())
            self.channel = grpc.secure_channel(self.endpoint, credentials, options=options)
        else:
            self.channel = grpc.insecure_channel(self.endpoint, options=options)
        self.stub = SimulationServiceStub(self.channel)

    # 关闭远端仿真服务的 gRPC 连接。
    def close(self):
        self.channel.close()

    # 带统一认证元数据和超时调用指定 gRPC 方法。
    def call(self, method, request=None, *, timeout=None):
        if request is None:
            request = Empty()
        return getattr(self.stub, method)(request, timeout=timeout or self.timeout,
                                         metadata=self.metadata)

    # 读取服务健康状态与契约版本。
    def health(self):
        return self.call('Health', pb.HealthRequest())

    # 幂等提交仿真请求，返回任务句柄。
    def submit(self, simulation, request_id):
        message = pb.SubmitJobRequest(request_id=request_id)
        if isinstance(simulation, pb.SimulateRequest):
            message.simulation.CopyFrom(simulation)
        else:
            ParseDict(simulation, message.simulation)
        return self.call('SubmitJob', message)

    # 按任务标识读取远程任务的最新状态与产物清单。
    def get(self, job_id):
        return self.call('GetJob', pb.GetJobRequest(job_id=job_id))

    # 向服务请求取消任务，由服务决定排队或运行中的任务能否取消。
    def cancel(self, job_id):
        return self.call('CancelJob', pb.GetJobRequest(job_id=job_id))

    # 确认产物已在本地校验落盘，允许服务端回收磁盘。
    def ack(self, job_id, *, timeout=None):
        return self.call('AckJob', pb.AckJobRequest(job_id=job_id), timeout=timeout)

    # 可选的服务端推送；轮询仍是主要机制。
    def watch(self, job_id, *, timeout=None):
        return self.stub.WatchJob(pb.GetJobRequest(job_id=job_id),
                                  timeout=timeout or self.timeout, metadata=self.metadata)

    # 轮询任务直至终态、超时或停止，区分显式取消和仅停止本地观察。
    def wait(self, job, *, stop=None, on_status=lambda job: None, timeout=None):
        deadline = time.monotonic() + (self.job_timeout if timeout is None else timeout)
        started = time.monotonic()
        while True:
            on_status(job)
            if job_state(job) in TERMINAL:
                if job_state(job) != 'succeeded':
                    raise RemoteJobFailed(job)
                return job
            if stop is not None and stop.is_set():
                # Host shutdown/lease loss only disconnects observation. Explicit
                # run cancellation asks the service, without claiming a GPU abort.
                if getattr(stop, 'canceled', False):
                    try:
                        cancelled = self.cancel(job.id)
                        on_status(cancelled)
                    except grpc.RpcError as error:
                        if error.code() != grpc.StatusCode.FAILED_PRECONDITION:
                            raise
                raise RunCancelled(f'Isaac Sim observation detached from {job.id}; receipt retained')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'Isaac Sim job {job.id} still exists; resume the retained receipt')
            time.sleep(min(poll_interval(time.monotonic() - started), remaining))
            try:
                job = self.get(job.id)
            except grpc.RpcError as error:
                if error.code() not in TRANSIENT:
                    raise

    # 在回执锁内下载任务清单指定的产物，支持校验偏移和断点续传。
    def download(self, job, directory, *, stop=None):
        """Download manifest identities with checked offsets, resumable files and receipts."""
        with receipt_lock(directory):
            return self._download(job, directory, stop=stop)

    # 校验不可变产物清单，逐项验证身份、流偏移、长度和摘要并保存下载回执。
    def _download(self, job, directory, *, stop=None):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        manifest = document(job)
        manifest_path = directory / 'job.json'
        if manifest_path.exists():
            import json
            previous = json.loads(manifest_path.read_text())
            if previous.get('id') != manifest.get('id') or previous.get('artifacts') != manifest.get('artifacts'):
                raise ValueError('Immutable Isaac Sim artifact manifest changed')
        write_json(manifest_path, manifest)
        if not job.artifacts:
            raise ValueError('Isaac Sim succeeded job has no artifact manifest')
        files, identities = [], set()
        for item in job.artifacts:
            if not item.artifact_id or item.artifact_id in identities:
                raise ValueError('Duplicate or empty Isaac Sim artifact identity')
            identities.add(item.artifact_id)
            if not item.sha256:
                raise ValueError('Isaac Sim artifact manifest omitted sha256')
            name = item.name or Path(item.relative_path).name
            # Native manifests may contain equal basenames from different cameras
            # or samples. Identity directories preserve both files without guessing.
            target = safe_path(safe_path(directory, item.artifact_id), name)
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = safe_path(target.parent, '.' + target.name + '.partial')
            size = int(item.size_bytes)
            if not target.exists():
                offset = partial.stat().st_size if partial.exists() else 0
                if offset > size:
                    raise ValueError('Partial artifact exceeds its immutable manifest size')
                if offset < size:
                    self._stream(job.id, item, partial, offset, stop=stop)
                elif size == 0:
                    partial.touch()
                if partial.stat().st_size != size:
                    raise ValueError('Isaac Sim artifact stream ended before its manifest size')
                with partial.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                if digest != item.sha256.lower():
                    partial.unlink()
                    raise ValueError('Isaac Sim artifact sha256 does not match its manifest')
                partial.replace(target)
            else:
                if target.stat().st_size != size:
                    raise ValueError('Downloaded Isaac Sim artifact size changed')
                with target.open('rb') as stream:
                    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != item.sha256.lower():
                raise ValueError('Downloaded Isaac Sim artifact sha256 does not match its manifest')
            files.append({'artifact_id': item.artifact_id, 'name': name,
                          'relative_path': item.relative_path,
                          'source_path': str(target), 'size_bytes': size,
                          'sha256': digest, 'media_type': item.media_type})
        write_json(directory / 'files.json', {'job_id': job.id, 'files': files})
        return files

    # 将单个产物流式写入 partial 文件，严格校验块偏移与清单长度。
    def _stream(self, job_id, item, partial, offset, *, stop=None):
        size = int(item.size_bytes)
        stream = self.stub.DownloadArtifact(pb.DownloadArtifactRequest(
            job_id=job_id, artifact_id=item.artifact_id, offset=offset,
            chunk_bytes=self.chunk_bytes),
            timeout=max(self.timeout, 300), metadata=self.metadata)
        try:
            with partial.open('ab') as output:
                for block in stream:
                    if stop is not None and stop.is_set():
                        raise RunCancelled('Isaac Sim download paused; partial bytes retained')
                    if block.offset != offset or offset + len(block.data) > size:
                        raise ValueError('Isaac Sim artifact stream has an invalid offset/size')
                    offset += output.write(block.data)
                output.flush()
                os.fsync(output.fileno())
        finally:
            stream.cancel()
