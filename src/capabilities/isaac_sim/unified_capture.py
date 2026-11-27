"""Run a supported S2 SceneProgram as a single durable multi-camera job."""

import json
import math
import shutil

import grpc

from capabilities.isaac_sim.grpc.unified_tasks import DurableJobs, request_id
from capabilities.isaac_sim.grpc.unified_transport import PROTOCOL, job_state
from capabilities.isaac_sim.capture_contract import capture_service_client, service_health
from capabilities.isaac_sim.unified_mapping import select_snapshot_plan, simulation_request
from capabilities.isaac_sim.unified_evidence import entities_from_job, frame_evidence, physics_summary, resolve_artifact
from capabilities.isaac_sim.native_geometry import exported_entities
from capabilities.scene_authoring.validation.program import validate_program
from service_access.progress import record_progress
from service_access.request_context import check_cancelled
from storage.files import write_json


def try_unified(store, task, stop, directory, attempt, program, settings, scope, token, previous,
                client=None, preview=False):
    from service_access.configuration import isaac_unified_settings
    if previous and (previous.get('protocol') == 'a100sim.v1.Simulator' or
                     previous.get('protocol') == PROTOCOL and previous.get('mode') == 'persistent_scene'):
        return False
    validate_program(program)
    config = isaac_unified_settings(settings)
    own = client is None
    client = client or capture_service_client({'providers': {'IsaacSim4A100': config}}, snapshot=True)
    try:
        if previous:
            # Job receipts bind the exact wire request; rebuilding would change old defaults.
            if previous.get('protocol') != PROTOCOL or 'plan' not in previous:
                raise ValueError('Unrecognized frozen Isaac Sim capture protocol or mode')
            expected = config
            if 'mode' not in previous:
                expected = {**settings, **settings.get('unified_capture', {})}
                expected['render'] = {**(expected.get('render') or {}),
                    'image_size': expected.get('image_size', 1024), 'samples_per_pixel':
                    (program.get('render_environment') or {}).get('samples_per_pixel', expected.get('samples_per_pixel', 32))}
            if (previous.get('settings') != expected or previous.get('program') != program
                    or previous.get('capture_scope') != scope or previous.get('capture_token') != token):
                raise ValueError('Frozen unified capture inputs changed; use a new attempt')
            plan = previous['plan']
        else:
            plan = select_snapshot_plan(program, config, scope, service_health(client),
                                        assets_root=store.root / 'generated_assets', preview=preview)
            if plan is None:
                return False
            write_json(attempt / 'capture.json', {'protocol': PROTOCOL, 'mode': 'shared_snapshot',
                       'program': program, 'settings': config, 'capture_scope': scope,
                       'capture_token': token, 'plan': plan})
        record_progress(directory, 'isaac_sim_preparing', protocol=PROTOCOL, mode='shared_snapshot')
        jobs = DurableJobs(client, attempt / 'jobs', token, stop=stop,
            on_status=lambda job: record_progress(directory, 'isaac_sim_capture',
                job_id=job.id, operation='scene.capture', remote_state=job_state(job),
                phase=job.phase, frames_done=job.frames_done, frames_total=job.frames_total))
        key = 'views-0'
        check_cancelled(stop, 'unified capture submission')
        if plan['views']:
            store.reserve_capture(task['id'], token, program,
                                  retained=(attempt / 'jobs' / key / 'receipt.json').is_file())
        job = jobs.wait(key, jobs.submit(key, simulation_request(plan)))
        files = jobs.download(key, job)
        result = json.loads(resolve_artifact(job.outputs.simulation_json, files).read_text())
        incoming = attempt / 'capture'
        incoming.mkdir(exist_ok=True)
        publish(store, task, stop, incoming, attempt, program, scope, plan, job, result, files, token)
        record_progress(directory, 'isaac_sim_captured', protocol=PROTOCOL, mode='shared_snapshot',
                        views=len(plan['views']), job_id=job.id, capture_directory=str(directory / 'capture'))
        try:
            client.ack(job.id)
        except grpc.RpcError as error:
            record_progress(directory, 'isaac_sim_ack_failed', job_id=job.id, detail=error.details())
        return True
    finally:
        if own:
            client.close()


def publish(store, task, stop, incoming, attempt, program, scope, plan, job, result, files, token):
    if job.steps != plan['steps'] or not math.isclose(job.dt, plan['dt']):
        raise ValueError('Simulation timing differs from the frozen capture request')
    frames = []
    snapshots = result.get('frames', [])
    captured = {int(frame['frame']): frame for frame in snapshots}
    expected = (set(plan['capture']['frames']) | {plan['steps']}) if plan['views'] else set()
    every = plan['capture'].get('every')
    if every:
        expected.update(range(every, plan['steps'] + 1, every))
    if set(captured) != expected or len(captured) != len(snapshots):
        raise ValueError('Unified service did not return the requested physics frames')
    for step, snapshot in sorted(captured.items()):
        if set(snapshot['views']) != {f"view_{view['index']}" for view in plan['views']}:
            raise ValueError('Unified service returned a different set of cameras')
        for view in plan['views']:
            frame = {**snapshot['views'][f"view_{view['index']}"], 'frame': step}
            frames.append(frame_evidence(frame, files, view['index'], view['camera'], program, plan, incoming,
                                         canonical=step == plan['steps'], physics_tick=step, dt=plan['dt'], job=job,
                                         render=result.get('render', {})))
    job_ids = [job.id]
    receipts = [{'job_id': job.id, 'request_id': request_id(token, 'views-0'),
                 'view_indices': scope['view_indices'], 'artifacts': files}]
    physics = physics_summary(job, plan['steps'], plan['dt'], plan)
    entities = entities_from_job(program, job, plan)
    source = resolve_artifact(job.outputs.scene, files)
    geometry = incoming / ('scene.usdc' if scope['scene_export_requested'] else 'native_geometry.usdc')
    shutil.copyfile(source, geometry)
    if scope['scene_export_requested']:
        from pxr import Sdf, UsdUtils
        # BodySpec meshes are baked into this layer. A sidecar needs the persistent asset-bundle route.
        if any(UsdUtils.ExtractExternalReferences(str(geometry))):
            raise ValueError('Snapshot USD contains external asset references; a complete native asset bundle is required')
        if not UsdUtils.CreateNewUsdzPackage(Sdf.AssetPath(str(geometry)), str(incoming / 'scene.usdz')):
            raise ValueError('Could not package the final simulation USD')
    prims = {body.id: body.prim_path for body in job.bodies}
    declared = {key: {**row, 'prim_path': prims[row['body_ids'][0]]} for key, row in entities.items()}
    entities = exported_entities(geometry, declared)
    for entity in entities.values():
        entity['bounds_status'] = 'measured_from_simulated_usd'
        entity['collision']['source'] = 'final simulation USD'
    if scope['scene_export_requested']:
        (incoming / 'scene.usda').write_text('#usda 1.0\n(subLayers = [@scene.usdc@])\n', encoding='utf-8')
    from capabilities.isaac_sim.evidence import common_render_metadata
    render = common_render_metadata(frames)
    evidence = {'schema': 'benchforge.evidence/v1', 'truth_domain': 'synthetic_world',
        'origin': 'isaacsim_rpc', 'token': token, 'protocol': PROTOCOL, 'mode': 'shared_snapshot',
        'job_ids': job_ids, 'physics': physics, 'entities': entities, 'frames': frames,
        'render_settings': render, 'capture_scope': scope,
        'capture_plan': plan['capture'], 'asset_dependencies': [
            {'asset_id': b['asset_id'], 'entity_id': b['id'], 'object_id': b['id'],
             'source': 'registered_simulation_mesh'} for b in plan['bodies'] if 'asset_id' in b],
        'material_dependencies': [], 'realism': {
            'mass_and_contact_properties': 'uncalibrated synthetic priors',
            'static_body_semantics': 'kinematic',
            'material_semantics': 'base_color_only',
            'sim2real_gap': 'not measured'},
        'interaction': {'validated': False,
            'reason': 'no executable interaction requested', 'action_results': []}}
    views = len(plan['views'])
    report = {'token': token, 'status': 'captured',
        'renderable': all(frame['quality']['passed'] for frame in frames) if frames else None,
        'frames': len(frames), 'views': views, 'process_frames': len(frames) - views,
        'physics': physics, 'errors': [], 'render_settings': render,
        'interaction': evidence['interaction'],
        'render_qa': {'assessed': bool(frames),
            'passed': all(frame['quality']['passed'] for frame in frames) if frames else None,
            'failed_views': [{'frame_id': frame['frame_id'], 'reasons': frame['quality']['reasons']}
                             for frame in frames if not frame['quality']['passed']]},
        'capture_scope': scope, 'capture_plan': plan['capture'],
        'protocol': PROTOCOL, 'mode': 'shared_snapshot', 'job_ids': job_ids, 'manifest': 'manifest.json',
        'scene_format': 'usdc' if scope['scene_export_requested'] else None, 'scene_resources': [],
        'native_receipts': str(attempt / 'jobs')}
    manifest = {'schema': 'benchforge.isaacsim_manifest/v1', 'protocol': PROTOCOL,
                'capture_token': token, 'capture_plan': plan['capture'],
                'jobs': receipts}
    write_json(incoming / 'evidence.json', evidence)
    write_json(incoming / 'report.json', report)
    write_json(incoming / 'program.json', program)
    write_json(incoming / 'manifest.json', manifest)
    check_cancelled(stop, 'capture publication')
    store.publish_capture(task['id'], token, incoming)
