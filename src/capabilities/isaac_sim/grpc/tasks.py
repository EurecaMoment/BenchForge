"""Durable request identity and restart recovery above the public A100 protocol."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time

import grpc

from capabilities.isaac_sim.grpc.receipts import receipt_lock
from capabilities.isaac_sim.grpc.transport import document, TRANSIENT
from service_access.request_context import RunCancelled
from storage.files import write_json


def request_id(namespace, key):
    return 'benchforge-' + hashlib.sha256((namespace + '\0' + key).encode()).hexdigest()


class DurableTasks:
    def __init__(self, client, directory, namespace, *, stop=None, on_status=lambda job: None):
        self.client, self.directory, self.namespace = client, Path(directory), namespace
        self.stop, self.on_status = stop, on_status

    def submit(self, key, payload, *, feature=False):
        if not re.fullmatch(r'[A-Za-z0-9_-][A-Za-z0-9_.-]{0,159}', key):
            raise ValueError('Invalid Isaac Sim receipt key')
        directory = self.directory / key
        identity = request_id(self.namespace, key)
        frozen = {'protocol': 'a100sim.v1.Simulator', 'endpoint': self.client.endpoint,
                  'request_id': identity, 'feature': feature, 'payload': payload}
        with receipt_lock(directory):
            file = directory / 'request.json'
            if file.exists() and json.loads(file.read_text()) != frozen:
                raise ValueError('Isaac Sim request changed; preserve its receipt and submit a new identity')
            if not file.exists():
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

    def wait(self, key, job, *, timeout=None):
        previous = None
        def changed(current):
            nonlocal previous
            stamp = (current.job_id, current.revision, current.state)
            if stamp != previous:
                write_json(self.directory / key / 'receipt.json', document(current))
                self.on_status(current)
                previous = stamp
        return self.client.wait(job, stop=self.stop, on_status=changed, timeout=timeout)

    def run(self, key, payload, *, feature=False):
        job = self.wait(key, self.submit(key, payload, feature=feature))
        return job, document(job)['result']

    def extension(self, key, operation, arguments):
        return self.run(key, {'extension': {'operation': operation, 'arguments': arguments}})

    def download(self, key, job):
        return self.client.download(job, self.directory / key / 'artifacts', stop=self.stop)
