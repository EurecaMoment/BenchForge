"""Retained tasks and their historical receipt format on SimulationService."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import time

import grpc
from google.protobuf.json_format import ParseDict

from capabilities.isaac_sim.grpc import native_pb2 as pb, simulation_pb2 as rpc
from capabilities.isaac_sim.grpc.unified_transport import SimulationClient, TRANSIENT, document
from service_access.request_context import RunCancelled
from storage.files import safe_path, write_json


TERMINAL = {pb.SUCCEEDED, pb.FAILED, pb.CANCELLED, pb.INTERRUPTED}


def native_job(envelope, job_id):
    """An RTX snapshot cannot be mistaken for a retained, session-owned job."""
    if (not envelope.HasField('native_job') or envelope.id != job_id
            or envelope.native_job.job_id != job_id):
        raise ValueError('Isaac Sim returned an inconsistent native job identity')
    return envelope.native_job


class RemoteJobFailed(RuntimeError):
    # 保留远程任务完整回执，并将失败状态、错误码和原因写入异常。
    def __init__(self, job):
        self.job = document(job)
        super().__init__(f"Isaac Sim job {job.job_id} {pb.JobState.Name(job.state)}: "
                         f"{job.failure.code}: {job.failure.message}")


class A100Client(SimulationClient):
    """Retained-session operations share the snapshot client's endpoint and credentials."""

    def submit(self, payload, request_id, *, feature=False):
        message = rpc.SubmitJobRequest(request_id=request_id)
        ParseDict(payload, message.native_feature if feature else message.native_task)
        handle = self.call('SubmitJob', message)
        if not handle.id:
            raise ValueError('Isaac Sim returned an empty job handle')
        job = self.get(handle.id)
        if job.request_id != request_id:
            raise ValueError('Isaac Sim returned another request identity')
        return job

    def get(self, job_id):
        return native_job(super().get(job_id), job_id)

    def cancel(self, job_id):
        return native_job(super().cancel(job_id), job_id)

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
            stream = self.stub.WatchJob(rpc.GetJobRequest(job_id=job.job_id,
                                        after_revision=job.revision),
                                        timeout=min(3, remaining), metadata=self.metadata)
            try:
                for envelope in stream:
                    current = native_job(envelope, job.job_id)
                    if current.request_id != job.request_id or current.revision < job.revision:
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

    def _download_file(self, job, item, target, partial, stop):
        """Reconnect read-only streams at their committed local offset."""
        deadline = None
        delay = .25
        while not target.exists():
            if stop is not None and stop.is_set():
                raise RunCancelled('Isaac Sim download paused; partial bytes retained')
            offset = partial.stat().st_size if partial.exists() else 0
            if offset > item.size_bytes:
                raise ValueError('Partial artifact exceeds its immutable manifest size')
            request = rpc.DownloadArtifactRequest(
                job_id=job.job_id, artifact_id=item.artifact_id, offset=offset,
                chunk_bytes=min(self.chunk_bytes, 1024 * 1024))
            stream = self.call('DownloadArtifact', request)
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
            except grpc.RpcError as error:
                if error.code() not in TRANSIENT:
                    raise
                if deadline is None:
                    deadline = time.monotonic() + self.timeout
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise
                time.sleep(min(delay, remaining))
                delay = min(delay * 2, 2)
            finally:
                stream.cancel()

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
            self._download_file(job, item, target, partial, stop)
            if target.stat().st_size != item.size_bytes:
                raise ValueError('Downloaded Isaac Sim artifact size changed')
            with target.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            files.append({'artifact_id': item.artifact_id, 'name': item.name,
                          'source_path': str(target), 'size_bytes': item.size_bytes,
                          'sha256': digest, 'media_type': item.media_type})
        write_json(directory / 'files.json', {'job_id': job.job_id, 'files': files})
        return files
