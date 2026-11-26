"""Scene capture through the isaacsim_rpc.v1 SimulationService."""
from __future__ import annotations

import json
import shutil
import time

import grpc

from capabilities.capture.capture_scope import capture_scope
from capabilities.isaac_sim.capture_contract import (
    capture_service_client, health_issues, require_capture_service,
)
from capabilities.isaac_sim.evidence import entities_from_job, frame_evidence, physics_summary
from capabilities.isaac_sim.grpc.receipts import receipt_lock
from capabilities.isaac_sim.grpc.tasks import DurableJobs, request_id
from capabilities.isaac_sim.grpc.transport import PROTOCOL, document, job_state
from capabilities.isaac_sim.scene_mapping import simulation_plan, simulation_request
from capabilities.isaac_sim.scene_requirements import UnsupportedScene, validate_scene_requirements
from service_access.configuration import isaac_config
from service_access.progress import record_progress
from service_access.request_context import check_cancelled
from storage.files import write_json


# 校验多个相机任务的物理结果一致，防止不同渲染请求产生分歧的仿真。
def _require_same_physics(first, job):
    reference = {body.id: tuple(body.pose_xyzw) for body in first.bodies}
    current = {body.id: tuple(body.pose_xyzw) for body in job.bodies}
    if set(reference) != set(current):
        raise ValueError('Isaac Sim camera jobs returned different rigid bodies')
    for identity, pose in reference.items():
        other = current[identity]
        if len(pose) != len(other) or any(abs(left - right) > 1e-4
                                          for left, right in zip(pose, other)):
            raise ValueError('Isaac Sim camera jobs diverged; retry with one camera per scene')


# 在物理仿真与多相机渲染后，逐帧生成证据、清单并发布本地采集。
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
            store.update(task['id'], state='READY',
                         unit={**unit, 'phase': 'review', 'capture_token': token})
            return
        program = json.loads((directory / 'program.json').read_text())
        settings = isaac_config(config)
        scope = capture_scope(program, unit['intent'].get('capture_options'))
        validate_scene_requirements(program, settings=settings)
        plan = simulation_plan(program, settings, scope)
        if not plan['views']:
            raise UnsupportedScene('capture requires at least one camera view')
        frozen = {'program': program, 'capture_scope': scope, 'settings': settings,
                  'protocol': PROTOCOL, 'capture_token': token, 'plan': plan}
        frozen_path = attempt / 'capture.json'
        if frozen_path.exists() and json.loads(frozen_path.read_text()) != frozen:
            raise ValueError('Frozen capture inputs/configuration changed; an explicit new attempt is required')
        write_json(frozen_path, frozen)
        record_progress(directory, 'isaac_sim_preparing', protocol=PROTOCOL,
                        every=plan['capture']['every'], frames=plan['capture']['frames'])
        own_client = client is None
        client = client or capture_service_client(config)
        jobs = DurableJobs(client, attempt / 'jobs', token, stop=stop,
            on_status=lambda job: record_progress(directory, 'isaac_sim_capture',
                job_id=job.id, phase=job.phase, remote_state=job_state(job),
                frames_done=job.frames_done, frames_total=job.frames_total,
                receipt=str(attempt / 'jobs')))
        try:
            require_capture_service(program, settings, scope)
            health = document(client.health())
            issues = health_issues(health)
            if issues:
                raise UnsupportedScene('\n'.join(row['message'] for row in issues), issues=issues)
            incoming = attempt / 'capture'
            if incoming.exists():
                shutil.rmtree(incoming)
            incoming.mkdir(parents=True)
            frames, receipts, job_ids, completed = [], [], [], []
            first_job = None
            for view in plan['views']:
                key = f"view-{view['index']}"
                request = simulation_request(plan, view)
                job = jobs.submit(key, request)
                job = jobs.wait(key, job)
                files = jobs.download(key, job)
                if first_job is None:
                    first_job = job
                else:
                    _require_same_physics(first_job, job)
                captured = sorted(job.outputs.frames, key=lambda frame: frame.frame)
                if not captured:
                    raise ValueError('Isaac Sim succeeded job did not return frame artifacts')
                canonical_step = captured[-1].frame
                for frame in captured:
                    frames.append(frame_evidence(frame, files, view['index'], view['camera'],
                        program, plan, incoming, canonical=frame.frame == canonical_step,
                        physics_tick=frame.frame, dt=plan['dt'], job=job))
                receipts.append({'job_id': job.id, 'request_id': request_id(token, key),
                                 'camera_index': view['index'], 'state': job_state(job),
                                 'artifacts': files, 'fetched_at': _timestamp()})
                job_ids.append(job.id)
                completed.append(job)
            physics = physics_summary(first_job, plan['steps'], plan['dt'], plan)
            entities = entities_from_job(program, first_job, plan)
            render = {'renderer': first_job.render.renderer or 'Isaac Sim RTX',
                      'backend': first_job.render.backend or 'isaacsim_rpc.v1',
                      'native_rtx': bool(first_job.native_rtx), 'replicator': True,
                      'samples_per_pixel': plan['render']['spp'],
                      'resolution': [plan['render']['size']] * 2,
                      'fov_degrees': plan['render']['fov_degrees'],
                      'fov_source': 'configured_default'}
            evidence = {'schema': 'benchforge.evidence/v1', 'truth_domain': 'synthetic_world',
                'origin': 'isaacsim_rpc', 'token': token, 'protocol': PROTOCOL,
                'job_ids': job_ids, 'physics': physics, 'entities': entities, 'frames': frames,
                'render_settings': render, 'capture_scope': scope,
                'capture_plan': plan['capture'], 'asset_dependencies': [],
                'material_dependencies': [], 'realism': {
                    'mass_and_contact_properties': 'uncalibrated synthetic priors',
                    'static_body_semantics': 'upstream_default',
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
            # Local publish is authoritative. Ack only after the immutable capture
            # exists so a failed acknowledgement cannot strand unverified bytes.
            for job_id in job_ids:
                try:
                    client.ack(job_id)
                except grpc.RpcError as error:
                    record_progress(directory, 'isaac_sim_ack_failed', job_id=job_id,
                                    detail=error.details() or error.code().name)
            record_progress(directory, 'isaac_sim_captured', views=views,
                            frames=len(frames), capture_directory=str(destination),
                            job_ids=job_ids)
        finally:
            if own_client:
                client.close()


# 使用 UTC 秒级时间戳记录产物拉取时刻，保证清单可比较。
def _timestamp():
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
