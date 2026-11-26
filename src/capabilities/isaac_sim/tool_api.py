"""Workspace operation used by the ordinary DSH Python tool executor."""
from pathlib import Path
import hashlib

from capabilities.isaac_sim.grpc.tasks import DurableJobs, request_id
from capabilities.isaac_sim.grpc.transport import (
    RemoteJobFailed, SimulationClient, document, job_state,
)
from capabilities.isaac_sim.guide import usage
from storage.files import safe_path, write_json
from storage.native_jobs import NativeJobs
from storage.operation_outputs import operation_workspace, persistent_outputs, logical_path


# 暴露 v2 仿真服务入口，按会话核对任务归属并保留可恢复回执。
def execute(args, directory, config):
    directory = Path(directory)
    workspace = operation_workspace(directory)
    action = args.get('action', 'guide')
    session = (args.get('_dsh_context') or {}).get('session_id') or 'local-cli'
    client = None
    try:
        if action == 'guide':
            result = usage(args.get('operation'))
            write_json(directory / 'guide.json', result)
            return {'action': action, 'result': result, 'receipt_path': str(directory / 'guide.json')}
        client = SimulationClient(config)
        if action == 'health':
            result = document(client.health())
            write_json(directory / 'health.json', result)
            return {'action': action, 'result': result, 'receipt_path': str(directory / 'health.json')}
        if action == 'submit':
            owners = NativeJobs(workspace)
            key = args.get('request_id')
            context = args.get('_dsh_context') or {}
            if not key:
                if not context.get('session_id') or not context.get('call_id'):
                    raise ValueError('Supply a stable request_id for calls outside DSH')
                key = context['session_id'] + ':' + context['call_id']
            simulation = args.get('simulation')
            if not isinstance(simulation, dict):
                raise ValueError('submit requires a simulation object from the v2 contract')
            identity = hashlib.sha256((session + '\0' + key).encode()).hexdigest()
            receipt_root = workspace / '.benchforge' / 'isaac_sim' / identity
            with persistent_outputs(receipt_root, config, kind='native-requests') as private:
                # The directory may change across deployments; native identity
                # remains derived from the stable workspace/session/key.
                namespace = str(workspace.resolve()) + '\0' + session
                jobs = DurableJobs(client, private.parent, namespace)
                job = jobs.submit(identity, simulation)
                owners.remember(client.endpoint, job, session,
                                request_id=request_id(namespace, identity))
                return {'action': action, 'job': document(job),
                        'receipt_path': str(logical_path(jobs.directory / identity / 'receipt.json')),
                        'next_action': 'wait', 'accepted_is_complete': False}
        if action in ('status', 'simulation', 'wait', 'cancel', 'download', 'ack'):
            owners = NativeJobs(workspace)
            job_id = args.get('job_id')
            if not isinstance(job_id, str) or not job_id:
                raise ValueError('Supply the job_id returned by submit')
            owners.require(client.endpoint, job_id, session)
            job = client.get(job_id)
            if action == 'cancel':
                job = client.cancel(job_id)
            elif action == 'ack':
                released = client.ack(job_id)
                result = {'action': action, 'job': document(job),
                          'released': released.released, 'freed_bytes': released.freed_bytes}
                write_json(directory / 'job.json', document(job))
                result['receipt_path'] = str(directory / 'job.json')
                return result
            elif action == 'wait':
                timeout = args.get('timeout_seconds', 30)
                if type(timeout) not in (int, float) or not 0 < timeout <= 60:
                    raise ValueError('A tool observation waits for 0..60 seconds; it never resubmits')
                try:
                    job = client.wait(job, timeout=timeout)
                except (RemoteJobFailed, TimeoutError):
                    job = client.get(job_id)
            result = {'action': action, 'job': document(job)}
            if action == 'download':
                if job_state(job) != 'succeeded':
                    raise ValueError('Only a succeeded Isaac Sim job can publish artifacts')
                destination = safe_path(directory / 'artifacts', job_id)
                result['files'] = client.download(job, destination)
                result['artifacts'] = [{'path': item['source_path'],
                                        'title': 'Isaac Sim · ' + item['name']}
                                       for item in result['files']]
            write_json(directory / 'job.json', document(job))
            result['receipt_path'] = str(directory / 'job.json')
            return result
        raise ValueError('Unknown Isaac Sim action; use guide, health, submit, simulation, '
                         'status, wait, cancel, download or ack')
    finally:
        if client is not None:
            client.close()
