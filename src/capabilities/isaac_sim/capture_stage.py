"""Scene capture as an ordinary leased backend stage using native A100 jobs."""
from __future__ import annotations

import hashlib
import json
import shutil
import uuid

from capabilities.capture.capture_scope import capture_scope
from capabilities.isaac_sim.authoring import compile_scene, UnsupportedScene
from capabilities.isaac_sim.evidence import measured_entities, physics_summary, resolve_artifact, save_frame
from capabilities.isaac_sim.native_geometry import exported_entities
from capabilities.isaac_sim.scene_requirements import validate_scene_requirements
from capabilities.isaac_sim.grpc.receipts import receipt_lock
from capabilities.isaac_sim.grpc.tasks import DurableTasks, request_id
from capabilities.isaac_sim.grpc.transport import A100Client, document
from service_access.configuration import isaac_config
from service_access.progress import record_progress
from service_access.request_context import check_cancelled
from storage.files import write_json


def _asset(client, source, directory):
    receipt = directory / 'asset.json'
    with source.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    if receipt.exists():
        value = json.loads(receipt.read_text())
        if value['sha256'] != digest:
            raise ValueError('Frozen scene asset changed after upload')
        return {key: value[key] for key in ('asset_id', 'relative_path')}
    # Upload has no deduplication RPC. A lost upload reply may leave an unused
    # immutable file; only a verified receipt becomes part of a submitted job.
    value = client.upload(source, asset_id='benchforge-' + uuid.uuid4().hex)
    write_json(receipt, value)
    return {key: value[key] for key in ('asset_id', 'relative_path')}


def _inspect(requests, identity):
    prims, cursor = [], None
    for page in range(100):
        _, result = requests.extension(f'inspect-{page}', 'scene.inspect',
            {'session_id': identity, 'limit': 2000, **({'cursor': cursor} if cursor else {})})
        prims.extend(result['prims'])
        if not result.get('has_more'):
            return prims
        next_cursor = result.get('next_cursor')
        if not next_cursor or next_cursor == cursor:
            raise ValueError('Isaac Sim returned a nonadvancing scene inspection cursor')
        cursor = next_cursor
    raise ValueError('Native scene inspection exceeds 200000 prims')


def _native_imports(requests, identity, compiled):
    for index, item in enumerate(compiled['imports']):
        obj = item['object']
        binding = item['source']
        _, imported = requests.extension(f'asset-{index}', 'asset.import', {
            'session_id': identity, 'path': item['path'],
            'source': {key: binding[key] for key in ('asset_id', 'relative_path')},
            'fix_base': binding.get('fix_base', not obj['dynamic']),
            'mass_kg': obj['mass_kg'], 'physics': obj.get('physics', {}),
            'source_up_axis': item['catalog'].get('source_up_axis', 'Z')})
        # Only an explicitly configured source can represent a named native asset.
        scale = [obj['size'][i] / item['catalog']['size'][i] for i in range(3)]
        changes = [{'op': 'transform', 'path': item['path'], 'translation': item['center'],
                    'rotation_deg': [0, 0, obj.get('yaw_deg', 0)], 'scale': scale},
                   {'op': 'set', 'path': item['path'], 'attribute': 'a100sim:semanticLabel',
                    'type': 'string', 'value': obj['label']}]
        requests.extension(f'asset-pose-{index}', 'scene.edit', {'session_id': identity, 'changes': changes})
        compiled['entities'][obj['id']]['articulation_roots'] = imported.get('articulation_roots', [])


def execute_capture(store, task, stop, *, config, client=None):
    unit = task['unit']
    directory = store.directory(task['id'], unit['revision'])
    generation = unit.get('capture_generation', 0)
    attempt = directory / 'isaac_sim' / f'attempt_{generation}'
    with receipt_lock(attempt):
        check_cancelled(stop, 'capture admission')
        token = request_id(str(directory), f'capture-{generation}')
        destination = directory / 'capture'
        if (destination / 'report.json').is_file():
            report = json.loads((destination / 'report.json').read_text())
            if report.get('token') != token:
                raise ValueError('An immutable capture already exists for a different attempt')
            store.update(task['id'], state='READY', unit={**unit, 'phase': 'review', 'capture_token': token})
            return
        program = json.loads((directory / 'program.json').read_text())
        settings = isaac_config(config)
        scope = capture_scope(program, unit['intent'].get('capture_options'))
        validate_scene_requirements(program)
        frozen = {'program': program, 'capture_scope': scope, 'settings': settings,
                  'protocol': 'a100sim.v1.Simulator', 'capture_token': token}
        frozen_path = attempt / 'capture.json'
        if frozen_path.exists() and json.loads(frozen_path.read_text()) != frozen:
            raise ValueError('Frozen capture inputs/configuration changed; an explicit new attempt is required')
        write_json(frozen_path, frozen)
        record_progress(directory, 'isaac_sim_preparing', protocol='a100sim.v1.Simulator')
        compiled = compile_scene(program, store.root / 'generated_assets', settings)
        source = attempt / 'scene.usda'
        source.write_text(compiled['usd'], encoding='utf-8')
        own_client = client is None
        client = client or A100Client(config)
        requests = DurableTasks(client, attempt / 'jobs', token, stop=stop,
            on_status=lambda job: record_progress(directory, 'isaac_sim_capture',
                job_id=job.job_id, operation=job.operation, remote_state=document(job)['state'],
                receipt=str(attempt / 'jobs')))
        try:
            capability = document(client.call('GetCapabilities'))
            required = {'extension.asset.import', 'extension.physics.step',
                        'extension.scene.inspect', 'extension.scene.capture', 'extension.world.observe'}
            if compiled['imports'] or scope['scene_export_requested']:
                required.add('extension.scene.export')
            if compiled['imports']:
                required.add('extension.scene.edit')
            available = {item['name'] for item in capability['capabilities'] if item['availability'] == 'AVAILABLE'}
            if missing := required - available:
                raise UnsupportedScene('Isaac Sim service lacks required operations: ' + ', '.join(sorted(missing)))
            source_ref = _asset(client, source, attempt)
            _, imported = requests.extension('import', 'asset.import', {'source': source_ref, 'path': '/World/Scene'})
            identity = imported['session_id']
            write_json(attempt / 'session.json', {'session_id': identity, 'capture_token': token})
            _native_imports(requests, identity, compiled)
            steps, dt = 180, 1 / 60
            physics_job, physics_result = requests.extension('settle', 'physics.step',
                {'session_id': identity, 'steps': steps, 'dt': dt})
            body_paths = [item['prim_path'] for item in compiled['entities'].values()
                          if item['dynamic'] and not item.get('articulation_roots')]
            _, observed = requests.extension('observe', 'world.observe',
                {'session_id': identity, 'bodies': body_paths, 'imu': False, 'contacts': False})
            physics = physics_summary(observed, steps, dt)
            prims = _inspect(requests, identity)
            incoming = attempt / 'capture'
            incoming.mkdir(exist_ok=True)
            frames, native_jobs = [], [physics_job.job_id]
            entities = measured_entities(compiled, prims)
            native = {key: value for key, value in compiled['entities'].items() if not value.get('geometry_parts')}
            if native:
                job, exported = requests.extension('geometry', 'scene.export', {'session_id': identity, 'format': 'usd'})
                files = requests.download('geometry', job)
                source_geometry = incoming / 'native_geometry.usdc'
                shutil.copyfile(resolve_artifact(exported['scene'], files), source_geometry)
                entities.update(exported_entities(source_geometry, native))
                native_jobs.append(job.job_id)
            views = scope['view_indices']
            # The service batches up to 32 cameras per request; retain every
            # authored view, split without reducing the user's camera count.
            for start in range(0, len(views), 32):
                indices = views[start:start + 32]
                key = f'views-{start}'
                job, captured = requests.extension(key, 'scene.capture', {
                    'session_id': identity, 'cameras': [compiled['cameras'][i] for i in indices],
                    'size': settings.get('image_size', 768), 'spp': compiled['samples_per_pixel'], 'annotations': True})
                files = requests.download(key, job)
                if len(captured.get('frames', [])) != len(indices):
                    raise ValueError('Isaac Sim did not return every requested camera')
                for index, frame in zip(indices, captured['frames']):
                    if frame['camera'] != compiled['cameras'][index]:
                        raise ValueError('Isaac Sim returned a different camera')
                    frames.append(save_frame(frame, files, index, program, incoming,
                        physics_tick=observed.get('physics_tick'), dt=observed.get('dt', dt)))
                native_jobs.append(job.job_id)
            if scope['scene_export_requested']:
                job, exported = requests.extension('export', 'scene.export', {'session_id': identity, 'format': 'usdz'})
                files = requests.download('export', job)
                shutil.copyfile(resolve_artifact(exported['scene'], files), incoming / 'scene.usdc')
                shutil.copyfile(resolve_artifact(exported['package'], files), incoming / 'scene.usdz')
                (incoming / 'scene.usda').write_text('#usda 1.0\n(subLayers = [@scene.usdc@])\n')
                native_jobs.append(job.job_id)
            render = {'renderer': 'A100 CUDA path tracer', 'native_rtx': False, 'replicator': False,
                      'samples_per_pixel': compiled['samples_per_pixel'],
                      'resolution': [settings.get('image_size', 768)] * 2}
            evidence = {'schema': 'benchforge.evidence/v1', 'truth_domain': 'synthetic_world',
                'origin': 'isaac_native', 'token': token, 'protocol': 'a100sim.v1.Simulator',
                'session_id': identity, 'job_ids': native_jobs, 'physics': physics,
                'entities': entities, 'frames': frames, 'render_settings': render, 'capture_scope': scope,
                'asset_dependencies': [], 'material_dependencies': [],
                'realism': {'mass_and_contact_properties': 'uncalibrated synthetic priors',
                            'sim2real_gap': 'not measured'},
                'interaction': {'validated': False, 'reason': 'no executable interaction requested', 'action_results': []}}
            report = {'token': token, 'status': 'captured',
                'renderable': all(frame['quality']['passed'] for frame in frames) if frames else None,
                'frames': len(frames), 'physics': physics, 'errors': [], 'render_settings': render,
                'interaction': evidence['interaction'],
                'render_qa': {'assessed': bool(frames),
                    'passed': all(frame['quality']['passed'] for frame in frames) if frames else None,
                    'failed_views': [{'frame_id': frame['frame_id'], 'reasons': frame['quality']['reasons']}
                                     for frame in frames if not frame['quality']['passed']]},
                'capture_scope': scope, 'scene_format': 'usdc', 'scene_resources': [],
                'native_receipts': str(attempt / 'jobs'), 'session_id': identity}
            write_json(incoming / 'evidence.json', evidence)
            write_json(incoming / 'report.json', report)
            write_json(incoming / 'program.json', program)
            check_cancelled(stop, 'capture publication')
            # Release the CUDA world before publishing. On reload the same
            # retained close request and immutable capture files are reused.
            requests.extension('close', 'scene.close', {'session_id': identity, 'delete': False})
            store.publish_capture(task['id'], token, incoming)
            record_progress(directory, 'isaac_sim_captured', views=len(frames),
                            capture_directory=str(destination), session_id=identity)
        finally:
            if own_client:
                client.close()
