"""Durable request identity and restart recovery for retained tasks on SimulationService."""
from __future__ import annotations

import json
from pathlib import Path
import re
import time

import grpc

from capabilities.isaac_sim.grpc.receipts import receipt_lock, request_id
from capabilities.isaac_sim.grpc.unified_transport import PROTOCOL, document, TRANSIENT
from service_access.request_context import RunCancelled
from storage.files import write_json


class DurableTasks:
    # 绑定远程客户端、回执目录、取消信号和状态回调。
    def __init__(self, client, directory, namespace, *, stop=None, on_status=lambda job: None):
        self.client, self.directory, self.namespace = client, Path(directory), namespace
        self.stop, self.on_status = stop, on_status

    # 冻结请求身份与内容，在回执锁内复用或提交远程任务。
    def submit(self, key, payload, *, feature=False):
        if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,159}', key):
            raise ValueError('Invalid Isaac Sim receipt key')
        directory = self.directory / key
        identity = request_id(self.namespace, key)
        frozen = {'protocol': PROTOCOL, 'endpoint': self.client.endpoint,
                  'request_id': identity, 'feature': feature, 'payload': payload}
        with receipt_lock(directory):
            file = directory / 'request.json'
            if file.exists():
                saved = json.loads(file.read_text())
                # Historical requests used the same native payload and stable request ID.
                if saved.get('protocol') == 'a100sim.v1.Simulator':
                    saved['protocol'] = PROTOCOL
                if saved != frozen:
                    raise ValueError('Isaac Sim request changed; preserve its receipt and submit a new identity')
            else:
                write_json(file, frozen)
            receipt = directory / 'receipt.json'
            if receipt.exists():
                previous = json.loads(receipt.read_text())
                job = self.client.get(previous['job_id'])
            else:
                # A lost reply is safe to retry only with these exact persisted bytes/ID.
                deadline = time.monotonic() + self.client.timeout
                while True:
                    if self.stop is not None and self.stop.is_set():
                        raise RunCancelled('Isaac Sim request retained before submission')
                    try:
                        job = self.client.submit(payload, identity, feature=feature)
                        break
                    except grpc.RpcError as error:
                        if error.code() not in TRANSIENT or time.monotonic() >= deadline:
                            raise
                        time.sleep(.25)
            if job.request_id != identity:
                raise ValueError('Isaac Sim returned another request identity')
            write_json(receipt, document(job))
        return job

    # 等待远程任务终态，并在版本或状态变化时持久化回执。
    def wait(self, key, job, *, timeout=None):
        previous = None
        # 仅在任务标识、修订或状态变化时更新回执并通知上层。
        def changed(current):
            nonlocal previous
            stamp = (current.job_id, current.revision, current.state)
            if stamp != previous:
                write_json(self.directory / key / 'receipt.json', document(current))
                self.on_status(current)
                previous = stamp
        return self.client.wait(job, stop=self.stop, on_status=changed, timeout=timeout)

    # 提交并等待一个持久任务，返回原生任务与JSON结果。
    def run(self, key, payload, *, feature=False):
        job = self.wait(key, self.submit(key, payload, feature=feature))
        return job, document(job)['result']

    # 把扩展操作及参数封装成持久任务执行。
    def extension(self, key, operation, arguments):
        return self.run(key, {'extension': {'operation': operation, 'arguments': arguments}})

    # 将已完成任务的产物下载到对应回执的独立目录。
    def download(self, key, job):
        return self.client.download(job, self.directory / key / 'artifacts', stop=self.stop)
