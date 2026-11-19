"""The A100 Simulator protocol. No simulator runtime or shared filesystem required."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time

import grpc
from google.protobuf.empty_pb2 import Empty
from google.protobuf.json_format import MessageToDict, ParseDict

from capabilities.isaac_sim.grpc import simulator_pb2 as pb
from capabilities.isaac_sim.grpc.receipts import receipt_lock
from capabilities.isaac_sim.grpc.simulator_pb2_grpc import SimulatorStub
from service_access.configuration import isaac_config, isaac_endpoint
from service_access.request_context import RunCancelled
from storage.files import safe_path, write_json


TERMINAL = {pb.SUCCEEDED, pb.FAILED, pb.CANCELLED, pb.INTERRUPTED}
TRANSIENT = {grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED,
             grpc.StatusCode.RESOURCE_EXHAUSTED}


# 保留协议字段名与默认值转换为JSON，并将负零规范化以兼容宿主。
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


class RemoteJobFailed(RuntimeError):
    # 保留远程任务完整回执，并将失败状态、错误码和原因写入异常。
    def __init__(self, job):
        self.job = document(job)
        super().__init__(f"Isaac Sim job {job.job_id} {pb.JobState.Name(job.state)}: "
                         f"{job.failure.code}: {job.failure.message}")


class A100Client:
    """A shared thread-safe channel; each independent scene owns its own request IDs."""

    # 从服务配置建立带认证与可选TLS的gRPC连接，设置请求和任务超时。
    def __init__(self, config):
        settings = isaac_config(config)
        connection = settings.get('grpc', {})
        self.endpoint = isaac_endpoint(connection)
        self.timeout = connection.get('timeout_seconds', 60)
        self.job_timeout = settings.get('capture_timeout_seconds', 1800)
        token_name = connection.get('token_env')
        token = os.environ.get(token_name, '') if token_name else ''
        if token_name and not token:
            raise ValueError(f'Isaac Sim token environment variable is unset: {token_name}')
        self.metadata = (('authorization', 'Bearer ' + token),) if token else ()
        certificate = connection.get('tls_ca_file')
        options = [('grpc.max_receive_message_length', 32 * 1024 * 1024),
                   ('grpc.enable_http_proxy', 0)]
        if certificate:
            credentials = grpc.ssl_channel_credentials(Path(certificate).read_bytes())
            self.channel = grpc.secure_channel(self.endpoint, credentials, options=options)
        else:
            self.channel = grpc.insecure_channel(self.endpoint, options=options)
        self.stub = SimulatorStub(self.channel)

    # 关闭原生模拟器的gRPC连接。
    def close(self):
        self.channel.close()

    # 带统一认证元数据和超时调用指定gRPC方法。
    def call(self, method, request=None, *, timeout=None):
        return getattr(self.stub, method)(request or Empty(), timeout=timeout or self.timeout,
                                         metadata=self.metadata)

    # 把普通或功能请求转换为协议消息，并附上幂等请求标识提交。
    def submit(self, payload, request_id, *, feature=False):
        message = pb.FeatureRequest() if feature else pb.SubmitTaskRequest()
        ParseDict({**payload, 'request_id': request_id}, message)
        return self.call('InvokeFeature' if feature else 'SubmitTask', message)

    # 按任务标识读取远程任务的最新状态。
    def get(self, job_id):
        return self.call('GetJob', pb.JobRequest(job_id=job_id))

    # 向服务请求取消任务，由服务决定排队任务能否取消。
    def cancel(self, job_id):
        """The server alone decides whether a queued job can be cancelled."""
        return self.call('CancelJob', pb.JobRequest(job_id=job_id))

    # 轮询任务直至终态、超时或停止，区分显式取消和仅停止本地观察。
    def wait(self, job, *, stop=None, on_status=lambda job: None, timeout=None):
        deadline = time.monotonic() + (self.job_timeout if timeout is None else timeout)
        while True:
            on_status(job)
            if job.state in TERMINAL:
                if job.state != pb.SUCCEEDED:
                    raise RemoteJobFailed(job)
                return job
            if stop is not None and stop.is_set():
                # Host shutdown/lease loss only disconnect observation. Explicit
                # run cancellation asks the service, without claiming a GPU abort.
                if getattr(stop, 'canceled', False):
                    try:
                        cancelled = self.cancel(job.job_id)
                        on_status(cancelled)
                    except grpc.RpcError as error:
                        if error.code() != grpc.StatusCode.FAILED_PRECONDITION:
                            raise
                raise RunCancelled(f'Isaac Sim observation detached from {job.job_id}; receipt retained')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f'Isaac Sim job {job.job_id} still exists; resume the retained receipt')
            stream = self.stub.WatchJob(pb.WatchJobRequest(job_id=job.job_id,
                                        after_revision=job.revision),
                                        timeout=min(3, remaining), metadata=self.metadata)
            try:
                for current in stream:
                    if current.job_id != job.job_id or current.revision < job.revision:
                        raise ValueError('Isaac Sim returned an inconsistent job stream')
                    job = current
                    on_status(job)
                    if job.state in TERMINAL or stop is not None and stop.is_set():
                        break
            except grpc.RpcError as error:
                if error.code() not in TRANSIENT:
                    raise
                if error.code() != grpc.StatusCode.DEADLINE_EXCEEDED:
                    time.sleep(min(.25, max(0, deadline - time.monotonic())))
            finally:
                stream.cancel()

    # 计算文件摘要并分块上传，返回服务登记的不可变资产句柄。
    def upload(self, path, *, asset_id='', relative_path=None):
        path = Path(path)
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        # 按256KiB产生上传块，仅首块携带资产标识、相对路径及摘要。
        def chunks():
            with path.open('rb') as stream:
                first = True
                while first or (data := stream.read(256 * 1024)):
                    if first:
                        data = stream.read(256 * 1024)
                    yield pb.UploadChunk(asset_id=asset_id if first else '',
                        relative_path=(relative_path or path.name) if first else '',
                        sha256=digest if first else '', data=data)
                    first = False
        result = self.call('UploadAsset', chunks())
        if result.sha256 != digest or result.size_bytes != path.stat().st_size:
            raise ValueError('Isaac Sim asset upload receipt does not match source bytes')
        return document(result)

    # 在回执锁内下载任务清单指定的产物，支持校验偏移和断点续传。
    def download(self, job, directory, *, stop=None):
        """Download manifest identities with checked offsets, resumable files and receipts."""
        with receipt_lock(directory):
            return self._download(job, directory, stop=stop)

    # 校验不可变产物清单，逐项验证身份、流偏移和摘要并保存下载回执。
    def _download(self, job, directory, *, stop=None):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        manifest = document(job)
        manifest_path = directory / 'job.json'
        if manifest_path.exists():
            import json
            previous = json.loads(manifest_path.read_text())
            if (previous.get('job_id'), previous.get('artifacts')) != (job.job_id, manifest['artifacts']):
                raise ValueError('Immutable Isaac Sim artifact manifest changed')
        write_json(manifest_path, manifest)
        files, identities = [], set()
        for item in job.artifacts:
            if item.artifact_id in identities:
                raise ValueError('Duplicate Isaac Sim artifact identity')
            identities.add(item.artifact_id)
            # Native manifests may contain equal basenames from different cameras
            # or samples. Identity directories preserve both files without guessing.
            target = safe_path(safe_path(directory, item.artifact_id), item.name)
            target.parent.mkdir(parents=True, exist_ok=True)
            partial = safe_path(target.parent, '.' + target.name + '.partial')
            if not target.exists():
                offset = partial.stat().st_size if partial.exists() else 0
                if offset > item.size_bytes:
                    raise ValueError('Partial artifact exceeds its immutable manifest size')
                stream = self.stub.DownloadArtifact(pb.DownloadRequest(job_id=job.job_id,
                    artifact_id=item.artifact_id, offset=offset),
                    timeout=self.timeout, metadata=self.metadata)
                try:
                    with partial.open('ab') as output:
                        for block in stream:
                            if stop is not None and stop.is_set():
                                raise RunCancelled('Isaac Sim download paused; partial bytes retained')
                            if block.offset != offset or offset + len(block.data) > item.size_bytes:
                                raise ValueError('Isaac Sim artifact stream has an invalid offset/size')
                            offset += output.write(block.data)
                        output.flush()
                        os.fsync(output.fileno())
                    if offset != item.size_bytes:
                        raise ValueError('Isaac Sim artifact stream ended before its manifest size')
                    partial.replace(target)
                finally:
                    stream.cancel()
            if target.stat().st_size != item.size_bytes:
                raise ValueError('Downloaded Isaac Sim artifact size changed')
            with target.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            files.append({'artifact_id': item.artifact_id, 'name': item.name,
                          'source_path': str(target), 'size_bytes': item.size_bytes,
                          'sha256': digest, 'media_type': item.media_type})
        write_json(directory / 'files.json', {'job_id': job.job_id, 'files': files})
        return files
