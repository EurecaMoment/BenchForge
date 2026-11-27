"""Run a supported S2 SceneProgram as a single durable multi-camera job."""

import json

import grpc

from capabilities.isaac_sim.grpc.unified_tasks import DurableJobs, request_id
from capabilities.isaac_sim.grpc.unified_transport import PROTOCOL, SimulationClient, job_state
from capabilities.isaac_sim.unified_mapping import simulation_plan, simulation_request
from capabilities.isaac_sim.unified_requirements import UnsupportedScene, capture_requirement_issues
from capabilities.isaac_sim.unified_evidence import entities_from_job, frame_evidence, physics_summary, resolve_artifact
from capabilities.scene_authoring.validation.program import validate_program
from service_access.progress import record_progress
from service_access.request_context import check_cancelled
from storage.files import write_json


def try_unified(store, task, stop, directory, attempt, program, settings, scope, token, previous, client=None):
    options = settings.get('unified_capture')
    pinned = previous is not None and previous.get('protocol') == PROTOCOL
    if pinned and not options:
        raise ValueError('Restore the configured unified endpoint to resume this frozen capture')
    if not options or previous is not None and not pinned:
        return False
    validate_program(program)
    config = {**settings, **options}
    config['render'] = {**(config.get('render') or {}),
                        'image_size': config.get('image_size', 1024),
                        'samples_per_pixel': (program.get('render_environment') or {}).get(
                            'samples_per_pixel', config.get('samples_per_pixel', 32))}
    issues = capture_requirement_issues(program, settings=config)
    # USD assembly, mesh scaling and active interactions require the retained service.
    retained = (any(obj.get('kind') not in ('box', 'sphere', 'mesh') or obj.get('physics')
                    or obj.get('mesh_transform') or obj.get('render_representation')
                    or obj.get('kind') == 'mesh' and (obj.get('material_id') or obj.get('appearance'))
                    for obj in program['objects'])
                or (program.get('render_environment') or {}).get('renderer') is not None)
    if retained or issues or not scope['view_indices'] or scope['scene_export_requested']:
        if pinned:
            raise ValueError('Frozen unified scene no longer satisfies the adapter contract')
        return False
    try:
        plan = simulation_plan(program, config, scope)
    except UnsupportedScene:
        if pinned:
            raise
        return False
    own = client is None
    client = client or SimulationClient({'providers': {'IsaacSim4A100': config}})
    try:
        try:
            health = client.health()
            needed = {'multiview_frames'}
            instance_capable = 'instance_masks' in health.capabilities or 'instance_ids' in health.capabilities
            available = (health.status == 'ready' and needed.issubset(health.capabilities)
                         and instance_capable
                         and health.max_views >= len(plan['views']))
            for key, limit_field in (('size', 'max_image_size'), ('spp', 'max_samples_per_pixel')):
                limit = getattr(health, limit_field, 0)
                if limit and plan['render'][key] > limit:
                    raise ValueError(f"Requested {key}={plan['render'][key]} exceeds {limit_field}={limit}")
        except grpc.RpcError as exc:
            if pinned or exc.code() != grpc.StatusCode.UNIMPLEMENTED:
                raise
            available = False
        if not available:
            if pinned:
                raise ValueError('Frozen unified capture requires multi-view and instance-mask support')
            return False
        frozen = {'protocol': PROTOCOL, 'program': program, 'settings': config,
                  'capture_scope': scope, 'capture_token': token, 'plan': plan}
        if previous is not None and previous != frozen:
            raise ValueError('Frozen unified capture inputs changed; use a new attempt')
        write_json(attempt / 'capture.json', frozen)
        jobs = DurableJobs(client, attempt / 'jobs', token, stop=stop,
            on_status=lambda job: record_progress(directory, 'isaac_sim_capture',
                job_id=job.id, operation='scene.capture', remote_state=job_state(job),
                frames_done=job.frames_done, frames_total=job.frames_total))
        key = 'views-0'
        check_cancelled(stop, 'unified capture submission')
        store.reserve_capture(task['id'], token, program,
                              retained=(attempt / 'jobs' / key / 'receipt.json').is_file())
        job = jobs.wait(key, jobs.submit(key, simulation_request(plan)))
        files = jobs.download(key, job)
        result = json.loads(resolve_artifact(job.outputs.simulation_json, files).read_text())
        incoming = attempt / 'capture'
        incoming.mkdir(exist_ok=True)
        publish(store, task, stop, incoming, attempt, program, scope, plan, job, result, files, token)
        try:
            client.ack(job.id)
        except grpc.RpcError as error:
            record_progress(directory, 'isaac_sim_ack_failed', job_id=job.id, detail=error.details())
        return True
    finally:
        if own:
            client.close()


def publish(store, task, stop, incoming, attempt, program, scope, plan, job, result, files, token):
    frames = []
    captured = {int(frame['frame']): frame for frame in result['frames']}
    expected = set(plan['capture']['frames']) | {plan['steps']}
    every = plan['capture'].get('every')
    if every:
        expected.update(range(every, plan['steps'] + 1, every))
    if set(captured) != expected or len(captured) != len(result['frames']):
        raise ValueError('Unified service did not return the requested physics frames')
    for step, snapshot in sorted(captured.items()):
        for view in plan['views']:
            frame = {**snapshot['views'][f"view_{view['index']}"], 'frame': step}
            frames.append(frame_evidence(frame, files, view['index'], view['camera'], program, plan, incoming,
                                         canonical=step == plan['steps'], physics_tick=step, dt=plan['dt'], job=job))
    job_ids = [job.id]
    receipts = [{'job_id': job.id, 'request_id': request_id(token, 'views-0'),
                 'view_indices': scope['view_indices'], 'artifacts': files}]
    physics = physics_summary(job, plan['steps'], plan['dt'], plan)
    entities = entities_from_job(program, job, plan)
    render = {'renderer': job.render.renderer or 'Isaac Sim RTX',
              'backend': job.render.backend or 'isaacsim_rpc.v1',
              'native_rtx': bool(job.native_rtx), 'replicator': bool(job.native_rtx),
              'samples_per_pixel': plan['render']['spp'],
              'resolution': [plan['render']['size']] * 2,
              'fov_degrees': plan['render']['fov_degrees'],
              'fov_source': 'simulator_camera_receipt'}
    evidence = {'schema': 'benchforge.evidence/v1', 'truth_domain': 'synthetic_world',
        'origin': 'isaacsim_rpc', 'token': token, 'protocol': PROTOCOL,
        'job_ids': job_ids, 'physics': physics, 'entities': entities, 'frames': frames,
        'render_settings': render, 'capture_scope': scope,
        'capture_plan': plan['capture'], 'asset_dependencies': [
            {'asset_id': b['asset_id'], 'object_id': b['id']} for b in plan['bodies'] if 'asset_id' in b],
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
        'protocol': PROTOCOL, 'job_ids': job_ids, 'manifest': 'manifest.json',
        'scene_format': None, 'scene_resources': [],
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
