"""Workspace operation used by the ordinary DSH Python tool executor."""
from pathlib import Path
import hashlib
import json

from capabilities.isaac_sim.grpc.tasks import DurableTasks
from capabilities.isaac_sim.grpc.transport import A100Client, RemoteJobFailed, document
from storage.files import safe_path, write_json
from storage.native_jobs import NativeJobs


def _legacy_submission(workspace, key, payload, feature, client, owners, session):
    """Reuse a pre-ownership receipt only after its session has been verified."""
    identity = hashlib.sha256(key.encode()).hexdigest()
    receipt = workspace / '.benchforge' / 'isaac_sim' / identity / 'receipt.json'
    if not receipt.is_file():
        if receipt.with_name('request.json').is_file():
            raise ValueError('Legacy submission has no receipt; recover its frozen request before retrying')
        return None
    saved = json.loads(receipt.read_text(encoding='utf-8'))
    try:
        request_id = owners.require(client.endpoint, saved['job_id'], session)
    except PermissionError:
        raise PermissionError('Legacy request ownership cannot be verified; use a new request_id for new work') from None
    frozen = json.loads(receipt.with_name('request.json').read_text(encoding='utf-8'))
    if (frozen.get('endpoint') != client.endpoint or frozen.get('payload') != payload
            or frozen.get('feature') != feature or frozen.get('request_id') != request_id):
        raise ValueError('Isaac Sim request changed; preserve its receipt and submit a new identity')
    job = client.get(saved['job_id'])
    if job.request_id != request_id:
        raise PermissionError('Native job request identity changed')
    return job, receipt


def execute(args, directory, config):
    """Expose the actual catalog and messages, without deployment-specific plugins."""
    directory = Path(directory)
    workspace = directory.parents[1]
    action = args.get('action', 'capabilities')
    client = A100Client(config)
    session = (args.get('_dsh_context') or {}).get('session_id') or 'local-cli'
    owners = NativeJobs(workspace)
    try:
        if action in ('capabilities', 'catalog', 'assets'):
            result = document(client.call({'capabilities': 'GetCapabilities',
                'catalog': 'GetApiCatalog', 'assets': 'ListAssets'}[action]))
            write_json(directory / f'{action}.json', result)
            if action == 'catalog' and args.get('feature_id'):
                rows = [feature for domain in result['domains'] for feature in domain['features']
                        if feature['id'] == args['feature_id']]
                if not rows:
                    raise ValueError('Feature is absent from the live Isaac Sim catalog')
                result = {'api_version': result['api_version'], 'features': rows}
            return {'action': action, 'result': result, 'receipt_path': str(directory / f'{action}.json')}
        if action == 'upload':
            source = (workspace / args['source_path']).resolve()
            if not source.is_relative_to(workspace.resolve()) or not source.is_file():
                raise ValueError('Upload source must be a saved file inside this conversation workspace')
            return {'action': action, 'asset': client.upload(source, asset_id=args.get('asset_id', ''),
                                                            relative_path=args.get('relative_path'))}
        if action in ('submit', 'invoke'):
            key = args.get('request_id')
            context = args.get('_dsh_context') or {}
            if not key:
                if not context.get('session_id') or not context.get('call_id'):
                    raise ValueError('Supply a stable request_id for calls outside DSH')
                key = context['session_id'] + ':' + context['call_id']
            payload = args.get('request', {})
            if action == 'invoke':
                payload = {'feature_id': args['feature_id'], 'arguments': payload}
            if 'request_id' in payload:
                raise ValueError('request_id belongs at the tool request root')
            previous = _legacy_submission(workspace, key, payload, action == 'invoke', client, owners, session)
            if previous:
                job, receipt = previous
                return {'action': action, 'job': document(job), 'receipt_path': str(receipt),
                        'next_action': 'wait', 'accepted_is_complete': False}
            identity = hashlib.sha256((session + '\0' + key).encode()).hexdigest()
            requests = DurableTasks(client, workspace / '.benchforge' / 'isaac_sim',
                                    str(workspace.resolve()) + '\0' + session)
            job = requests.submit(identity, payload, feature=action == 'invoke')
            owners.remember(client.endpoint, job, session)
            return {'action': action, 'job': document(job),
                    'receipt_path': str(requests.directory / identity / 'receipt.json'),
                    'next_action': 'wait', 'accepted_is_complete': False}
        if action in ('status', 'wait', 'cancel', 'download'):
            job_id = args['job_id']
            request_id = owners.require(client.endpoint, job_id, session)
            observed = client.get(job_id)
            if observed.request_id != request_id:
                raise PermissionError('Native job request identity changed')
            job = client.cancel(job_id) if action == 'cancel' else observed
            if action == 'wait':
                timeout = args.get('timeout_seconds', 30)
                if type(timeout) not in (int, float) or not 0 < timeout <= 60:
                    raise ValueError('A tool observation waits for 0..60 seconds; it never resubmits')
                try:
                    job = client.wait(job, timeout=timeout)
                except (RemoteJobFailed, TimeoutError):
                    job = client.get(job_id)
            result = {'action': action, 'job': document(job)}
            if action == 'download':
                from capabilities.isaac_sim.grpc import simulator_pb2 as pb
                if job.state != pb.SUCCEEDED:
                    raise ValueError('Only a succeeded native job can publish artifacts')
                destination = safe_path(workspace / 'artifacts' / 'isaac_sim', job_id)
                result['files'] = client.download(job, destination)
                # The native operation establishes what was produced; filenames
                # such as dataset.zip do not establish a question-generation stage.
                stage = {'extension.world.collect': 'S3', 'extension.scene.capture': 'S2',
                         'extension.scene.export': 'S2'}.get(job.operation)
                result['artifacts'] = [{'path': item['source_path'], 'title': 'Isaac Sim · ' + item['name'],
                                        **({'stage': stage} if stage else {})}
                                       for item in result['files']]
            write_json(directory / 'job.json', document(job))
            result['receipt_path'] = str(directory / 'job.json')
            return result
        raise ValueError('Unknown Isaac Sim action')
    finally:
        client.close()
