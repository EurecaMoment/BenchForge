"""Workspace operation used by the ordinary DSH Python tool executor."""
from pathlib import Path
import hashlib
import json

from capabilities.isaac_sim.grpc import native_pb2 as pb
from capabilities.isaac_sim.grpc.tasks import DurableTasks
from capabilities.isaac_sim.grpc.transport import A100Client, RemoteJobFailed, document
from capabilities.isaac_sim.guide import usage
from storage.files import safe_path, write_json
from storage.native_jobs import NativeJobs
from capabilities.isaac_sim.resource_access import NativeResources
from storage.operation_outputs import operation_workspace, persistent_outputs, logical_path


# 仅在会话所有权、冻结请求及远程身份完全匹配后复用旧版任务回执。
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


# 提供原生目录、上传与任务操作入口，按会话验证资源及任务所有权并保留可恢复回执。
def execute(args, directory, config):
    """Expose the actual catalog and messages, without deployment-specific plugins."""
    directory = Path(directory)
    workspace = operation_workspace(directory)
    action = args.get('action', 'capabilities')
    client = A100Client(config)
    resources = NativeResources(client.endpoint)
    session = (args.get('_dsh_context') or {}).get('session_id') or 'local-cli'
    try:
        if action == 'guide':
            result = usage(document(client.call('GetCapabilities')), document(client.call('GetApiCatalog')),
                           operation=args.get('operation'), feature_id=args.get('feature_id'))
            write_json(directory / 'guide.json', result)
            return {'action': action, 'result': result, 'receipt_path': str(directory / 'guide.json')}
        if action in ('capabilities', 'catalog', 'assets'):
            result = document(client.call({'capabilities': 'GetCapabilities',
                'catalog': 'GetApiCatalog', 'assets': 'ListAssets'}[action]))
            if action == 'assets' and resources.access is not None:
                result['assets'] = [asset for asset in result.get('assets', []) if resources.owns('asset', asset['asset_id'])]
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
            if args.get('asset_id'):
                resources.require('asset', args['asset_id'])
            warnings = []
            if resources.access is not None:
                from capabilities.isaac_sim.upload_access import snapshot_upload
                with snapshot_upload(source, args.get('relative_path'), warnings=warnings) as snapshot:
                    asset = client.upload(snapshot, asset_id=args.get('asset_id', ''),
                                          relative_path=args.get('relative_path') or source.name)
            else:
                asset = client.upload(source, asset_id=args.get('asset_id', ''), relative_path=args.get('relative_path'))
            resources.remember('asset', asset['asset_id'], origin='upload')
            return {'action': action, 'asset': asset,
                    **({'warnings': warnings} if warnings else {})}
        if action in ('submit', 'invoke'):
            owners = NativeJobs(workspace)
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
            resources.check(payload, feature=action == 'invoke')
            previous = _legacy_submission(workspace, key, payload, action == 'invoke', client, owners, session)
            if previous:
                job, receipt = previous
                return {'action': action, 'job': document(job), 'receipt_path': str(receipt),
                        'next_action': 'wait', 'accepted_is_complete': False}
            identity = hashlib.sha256((session + '\0' + key).encode()).hexdigest()
            receipt_root = workspace / '.benchforge' / 'isaac_sim' / identity
            with persistent_outputs(receipt_root, config, kind='native-requests') as private:
                # The directory may change across deployments; native identity
                # remains derived from the stable workspace/session/key.
                requests = DurableTasks(client, private.parent,
                                        str(workspace.resolve()) + '\0' + session)
                job = requests.submit(identity, payload, feature=action == 'invoke')
                owners.remember(client.endpoint, job, session)
                resources.observe(document(job))
                return {'action': action, 'job': document(job),
                        'receipt_path': str(logical_path(requests.directory / identity / 'receipt.json')),
                        'next_action': 'wait', 'accepted_is_complete': False}
        if action in ('status', 'wait', 'cancel', 'download'):
            owners = NativeJobs(workspace)
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
            if job.request_id != request_id:
                raise PermissionError('Native job request identity changed')
            result = {'action': action, 'job': document(job)}
            resources.observe(result['job'])
            if (action in ('status', 'wait') and job.state == pb.FAILED
                    and job.operation == 'extension.extended.scene.capture'
                    and job.failure.message == 'a perspective USD camera is required'):
                result['recovery_hint'] = (
                    'For a scene preview, reuse the saved SceneProgram and its existing cameras with '
                    'benchforge_capture(scene_program_path=...). For capture in this native session, '
                    'look up extension.extended.scene.inspect and extension.extended.scene.capture '
                    'with benchforge_isaac_sim(action="guide", operation=...). Use extended.scene.inspect '
                    'with the same session_id and put the exact paths of perspective Camera prims from '
                    'the inspection into cameras. If none exist, add or import a perspective USD camera '
                    'using the operations supported by the live guide, then inspect again. Submit '
                    'the corrected native capture with a new request_id.')
            if action == 'download':
                if job.state != pb.SUCCEEDED:
                    raise ValueError('Only a succeeded native job can publish artifacts')
                # Downloads belong to the current private output stage and are
                # published with its receipt after the complete operation.
                destination = safe_path(directory / 'artifacts', job_id)
                result['files'] = client.download(job, destination)
                # The native operation establishes what was produced; filenames
                # such as dataset.zip do not establish a question-generation stage.
                stage = {'extension.world.collect': 'S3', 'extension.scene.capture': 'S2',
                         'extension.scene.export': 'S2', 'extension.extended.world.collect': 'S3',
                         'extension.extended.scene.capture': 'S2',
                         'extension.extended.scene.export': 'S2'}.get(job.operation)
                result['artifacts'] = [{'path': item['source_path'], 'title': 'Isaac Sim · ' + item['name'],
                                        **({'stage': stage} if stage else {})}
                                       for item in result['files']]
            write_json(directory / 'job.json', result['job'])
            result['receipt_path'] = str(directory / 'job.json')
            return result
        raise ValueError('Unknown Isaac Sim action')
    finally:
        client.close()
