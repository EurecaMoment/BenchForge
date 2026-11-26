"""Durable request identity and restart recovery above the public SimulationService."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time

import grpc

from capabilities.isaac_sim.grpc.receipts import receipt_lock
from capabilities.isaac_sim.grpc.transport import PROTOCOL, TRANSIENT, document, job_state
from service_access.request_context import RunCancelled
from storage.files import write_json


# 由命名空间和操作键生成稳定请求标识，供远程服务实现幂等提交。
def request_id(namespace, key):
    return 'benchforge-' + hashlib.sha256((namespace + '\0' + key).encode()).hexdigest()


class DurableJobs:
    # 绑定远程客户端、回执目录、取消信号和状态回调。
    def __init__(self, client, directory, namespace, *, stop=None, on_status=lambda job: None):
        self.client, self.directory, self.namespace = client, Path(directory), namespace
        self.stop, self.on_status = stop, on_status

    # 冻结请求身份与内容，在回执锁内复用或提交远程任务。
    def submit(self, key, simulation):
        if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,159}', key):
            raise ValueError('Invalid Isaac Sim receipt key')
        directory = self.directory / key
        identity = request_id(self.namespace, key)
        frozen = {'protocol': PROTOCOL, 'endpoint': self.client.endpoint,
                  'request_id': identity, 'simulation': simulation}
        with receipt_lock(directory):
            file = directory / 'request.json'
            if file.exists() and json.loads(file.read_text()) != frozen:
                raise ValueError('Isaac Sim request changed; preserve its receipt and submit a new identity')
            if not file.exists():
                write_json(file, frozen)
            receipt = directory / 'receipt.json'
            if receipt.exists():
                previous = json.loads(receipt.read_text())
                job = self.client.get(previous['id'])
            else:
                # A lost reply is safe to retry only with these exact persisted bytes/ID.
                deadline = time.monotonic() + self.client.timeout
                while True:
                    if self.stop is not None and self.stop.is_set():
                        raise RunCancelled('Isaac Sim request retained before submission')
                    try:
                        handle = self.client.submit(simulation, identity)
                        break
                    except grpc.RpcError as error:
                        if error.code() not in TRANSIENT or time.monotonic() >= deadline:
                            raise
                        time.sleep(.25)
                job = self.client.get(handle.id)
            write_json(receipt, document(job))
        return job

    # 等待远程任务终态，并在状态或进度变化时持久化回执。
    def wait(self, key, job, *, timeout=None):
        previous = None
        # 仅在任务标识、状态、阶段或帧进度变化时更新回执并通知上层。
        def changed(current):
            nonlocal previous
            stamp = (current.id, job_state(current), current.phase,
                     current.frames_done, current.frames_total)
            if stamp != previous:
                write_json(self.directory / key / 'receipt.json', document(current))
                self.on_status(current)
                previous = stamp
        return self.client.wait(job, stop=self.stop, on_status=changed, timeout=timeout)

    # 提交并等待一个持久任务，返回原生任务与 JSON 结果。
    def run(self, key, simulation):
        job = self.wait(key, self.submit(key, simulation))
        return job, document(job)

    # 将已完成任务的产物下载到对应回执的独立目录。
    def download(self, key, job):
        return self.client.download(job, self.directory / key / 'artifacts', stop=self.stop)
