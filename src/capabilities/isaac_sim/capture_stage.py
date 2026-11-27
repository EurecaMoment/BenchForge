"""Scene capture as an ordinary leased backend stage using native A100 jobs."""
from __future__ import annotations

import json
import logging
import shutil

from capabilities.capture.capture_scope import capture_scope
from capabilities.isaac_sim.authoring import compile_scene
from capabilities.isaac_sim.evidence import (
    common_render_metadata, measured_entities, physics_summary, render_metadata, resolve_artifact, save_frame,
)
from capabilities.isaac_sim.native_geometry import exported_entities
from capabilities.isaac_sim.scene_requirements import UnsupportedScene, validate_scene_requirements
from capabilities.isaac_sim.capture_contract import (
    checked_service_issues, capture_service_client, native_edit_arguments, native_import_arguments,
)
from capabilities.isaac_sim.asset_bundle import (
    export_binary_usd, stage_texture_dependencies, upload_asset_bundle, write_mesh_layer,
)
from capabilities.isaac_sim.appearance_evidence import appearance_evidence, observe_render_sources
from capabilities.scene_authoring.catalog.native_reference import native_references
from capabilities.isaac_sim.grpc.receipts import receipt_lock
from capabilities.isaac_sim.grpc.tasks import DurableTasks, request_id
from capabilities.isaac_sim.grpc.transport import document
from capabilities.isaac_sim.grpc.unified_transport import PROTOCOL
from service_access.configuration import isaac_config, isaac_unified_settings
from service_access.progress import record_progress
from service_access.request_context import check_cancelled
from storage.files import write_json
from storage.scene_jobs.capture_budget import CaptureBudgetExhausted


# 分页读取原生场景节点，限制总量并拒绝不前进的游标。
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


# 依据编译清单导入原生资产，并通过显式编辑恢复场景尺寸、姿态和物理设置。
def _native_imports(requests, identity, compiled):
    for index, item in enumerate(compiled['imports']):
        obj = item['object']
        _, imported = requests.extension(f'asset-{index}', 'asset.import',
                                         native_import_arguments(item, identity))
        # Admission checks either a configured source or an owned upload handle.
        scale = [obj['size'][i] / item['catalog']['size'][i] for i in range(3)]
        requests.extension(f'asset-pose-{index}', 'scene.edit',
            native_edit_arguments(obj, item['path'], identity, center=item['center'], scale=scale))
        compiled['entities'][obj['id']]['articulation_roots'] = imported.get('articulation_roots', [])


def _capture_render_arguments(settings, compiled):
    return {'size': settings.get('image_size', 1024),
            'spp': compiled['samples_per_pixel'], 'annotations': True}


# 恢复队列任务的访问身份后编译场景，确保无HTTP上下文时仍按所有权消费原生句柄。
def _compile_for_task(store, task, program, settings, *, packed_meshes=None):
    """Restore queued ownership before consuming native handles without HTTP context."""
    if not native_references(program):
        return compile_scene(program, store.root / 'generated_assets', settings, packed_meshes=packed_meshes)
    from service_access.task_access import task_access
    from service_access.access_scope import access_context
    access = task_access(store.repo.engine, task['id'])
    with access_context(access):
        return compile_scene(program, store.root / 'generated_assets', settings, packed_meshes=packed_meshes)


# 以持久回执组织原生场景创建、仿真、渲染和下载，支持取消及中断后恢复。
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
        settings = dict(isaac_config(config))
        preview_settings = settings.pop('interactive_preview', None)
        scope = capture_scope(program, unit['intent'].get('capture_options'))
        frozen_path = attempt / 'capture.json'
        previous = json.loads(frozen_path.read_text()) if frozen_path.exists() else None
        from capabilities.isaac_sim.unified_capture import try_unified
        if try_unified(store, task, stop, directory, attempt, program, settings, scope, token, previous,
                       client=client, preview=bool(preview_settings and scope['view_indices'])):
            return
        historical = previous is not None and previous.get('protocol') == 'a100sim.v1.Simulator'
        if historical:
            settings.pop('unified_capture', None)
        else:
            settings = isaac_unified_settings(settings)
        validate_scene_requirements(program, settings=settings)
        frozen = {'program': program, 'capture_scope': scope, 'settings': settings,
                  'protocol': previous['protocol'] if historical else PROTOCOL, 'capture_token': token}
        if not historical:
            frozen['mode'] = 'persistent_scene'
        if previous is None or previous.get('mesh_encoding') == 'typed_usdc':
            frozen['mesh_encoding'] = 'typed_usdc'
        if previous is not None and previous != frozen:
            raise ValueError('Frozen capture inputs/configuration changed; an explicit new attempt is required')
        if previous is None:
            write_json(frozen_path, frozen)
        record_progress(directory, 'isaac_sim_preparing', protocol=PROTOCOL, mode='persistent_scene')
        packed_meshes = {} if 'mesh_encoding' in frozen else None
        compiled = _compile_for_task(store, task, program, settings, packed_meshes=packed_meshes)
        if not historical:
            compiled['samples_per_pixel'] = max(settings.get('samples_per_pixel', 256),
                                                (program.get('render_environment') or {}).get('samples_per_pixel', 0))
        if packed_meshes is None:
            # Existing attempts retain their original upload identity on resume.
            source = attempt / 'scene.usda'
            source.write_text(compiled['usd'], encoding='utf-8')
            upload_source = export_binary_usd(source, attempt / 'scene.usdc')
        else:
            upload_source = write_mesh_layer(compiled['usd'], packed_meshes, attempt / 'scene.usdc')
            packed_meshes.clear()
        texture_files = stage_texture_dependencies(
            compiled.get('texture_dependencies', []), attempt)
        own_client = client is None
        client = client or capture_service_client({'providers': {'IsaacSim4A100': settings}})
        requests = DurableTasks(client, attempt / 'jobs', token, stop=stop,
            on_status=lambda job: record_progress(directory, 'isaac_sim_capture',
                job_id=job.job_id, operation=job.operation, remote_state=document(job)['state'],
                receipt=str(attempt / 'jobs')))
        try:
            issues = checked_service_issues(client, compiled, scope, stop=stop,
                on_wait=lambda reason, delay: record_progress(directory, 'isaac_sim_waiting_for_worker',
                    reason=reason, retry_in_seconds=delay))
            if issues:
                raise UnsupportedScene('\n'.join(issue['message'] for issue in issues), issues=issues)
            source_ref = upload_asset_bundle(client, upload_source, attempt, texture_files)
            _, imported = requests.extension('import', 'asset.import', {'source': source_ref, 'path': '/World/Scene'})
            identity = imported['session_id']
            write_json(attempt / 'session.json', {'session_id': identity, 'capture_token': token})
            _native_imports(requests, identity, compiled)
            steps, dt = 180, 1 / 60
            physics_job, physics_result = requests.extension('settle', 'physics.step',
                {'session_id': identity, 'steps': steps, 'dt': dt})
            if preview_settings and scope['view_indices']:
                from capabilities.isaac_sim.interactive_preview import start_preview
                start_preview(config, preview_settings, identity, compiled['cameras'][scope['view_indices'][0]],
                              directory, generation)
            body_paths = [item['prim_path'] for item in compiled['entities'].values()
                          if item['dynamic'] and not item.get('articulation_roots')]
            _, observed = requests.extension('observe', 'world.observe',
                {'session_id': identity, 'bodies': body_paths, 'imu': False, 'contacts': False})
            physics = physics_summary(observed, steps, dt)
            prims = _inspect(requests, identity)
            incoming = attempt / 'capture'
            incoming.mkdir(exist_ok=True)
            frames, native_jobs = [], [physics_job.job_id]
            rendered_sources = {}
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
            if views:
                check_cancelled(stop, 'native capture submission')
                try:
                    store.reserve_capture(task['id'], token, program,
                                          retained=(attempt / 'jobs' / 'views-0' / 'receipt.json').is_file())
                except CaptureBudgetExhausted:
                    try:
                        requests.extension('close', 'scene.close', {'session_id': identity, 'delete': False})
                    except Exception:
                        logging.getLogger(__name__).warning('Could not close budget-blocked native scene %s',
                                                            identity, exc_info=True)
                    raise
            # The service batches up to 32 cameras per request; retain every
            # authored view, split without reducing the user's camera count.
            for start in range(0, len(views), 32):
                indices = views[start:start + 32]
                key = f'views-{start}'
                job, captured = requests.extension(key, 'scene.capture', {
                    'session_id': identity, 'cameras': [compiled['cameras'][i] for i in indices],
                    **_capture_render_arguments(settings, compiled)})
                files = requests.download(key, job)
                if len(captured.get('frames', [])) != len(indices):
                    raise ValueError('Isaac Sim did not return every requested camera')
                for index, frame in zip(indices, captured['frames']):
                    if frame['camera'] != compiled['cameras'][index]:
                        raise ValueError('Isaac Sim returned a different camera')
                    if list(frame['resolution']) != [settings.get('image_size', 1024)] * 2:
                        raise ValueError('Native frame resolution differs from the requested render size')
                    observed_frame = {**render_metadata(frame, captured), **frame}
                    frames.append(save_frame(observed_frame, files, index, program, incoming,
                        physics_tick=observed.get('physics_tick'), dt=observed.get('dt', dt)))
                    observe_render_sources(rendered_sources, frame)
                native_jobs.append(job.job_id)
                record_progress(directory, 'isaac_sim_capture', protocol=PROTOCOL,
                                mode='persistent_scene', operation='scene.capture', job_id=job.job_id,
                                frames_done=len(frames), frames_total=len(views))
            if scope['scene_export_requested']:
                job, exported = requests.extension('export', 'scene.export', {'session_id': identity, 'format': 'usdz'})
                files = requests.download('export', job)
                shutil.copyfile(resolve_artifact(exported['scene'], files), incoming / 'scene.usdc')
                shutil.copyfile(resolve_artifact(exported['package'], files), incoming / 'scene.usdz')
                (incoming / 'scene.usda').write_text('#usda 1.0\n(subLayers = [@scene.usdc@])\n')
                native_jobs.append(job.job_id)
            render = common_render_metadata(frames)
            evidence = {'schema': 'benchforge.evidence/v1', 'truth_domain': 'synthetic_world',
                'origin': 'isaac_native', 'token': token, 'protocol': PROTOCOL, 'mode': 'persistent_scene',
                'session_id': identity, 'job_ids': native_jobs, 'physics': physics,
                'entities': entities, 'frames': frames, 'render_settings': render, 'capture_scope': scope,
                **appearance_evidence(compiled, json.loads((attempt / 'asset.json').read_text()),
                                      prims, rendered_sources),
                'realism': {'mass_and_contact_properties': 'uncalibrated synthetic priors',
                            'sim2real_gap': 'not measured'},
                'interaction': {'validated': False, 'reason': 'no executable interaction requested', 'action_results': []}}
            report = {'token': token, 'status': 'captured', 'protocol': PROTOCOL, 'mode': 'persistent_scene',
                'renderable': all(frame['quality']['passed'] for frame in frames) if frames else None,
                'frames': len(frames), 'physics': physics, 'errors': [], 'render_settings': render,
                'interaction': evidence['interaction'],
                'render_qa': {'assessed': bool(frames),
                    'passed': all(frame['quality']['passed'] for frame in frames) if frames else None,
                    'failed_views': [{'frame_id': frame['frame_id'], 'reasons': frame['quality']['reasons']}
                                     for frame in frames if not frame['quality']['passed']]},
                'capture_scope': scope, 'scene_format': 'usdc' if scope['scene_export_requested'] else None,
                'scene_resources': [],
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
