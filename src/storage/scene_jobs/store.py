"""Persist scene production and capture state in the BenchForge database."""
import json
from pathlib import Path
import time
import uuid
from sqlalchemy import Table
from sqlalchemy import Column
from sqlalchemy import String
from sqlalchemy import JSON
from sqlalchemy import MetaData
from sqlalchemy import select
from sqlalchemy import update as sql_update
from storage.scene_jobs.metadata import Repository
from storage.scene_jobs.metadata import runs
from storage.scene_jobs.metadata import tasks
from storage.files import write_json
from storage.files import safe_path
from capabilities.scene_authoring.catalog.native import normalize_native_asset_references
from capabilities.scene_authoring.program_submission import submission_identity
from service_access.request_context import RunContext
from service_access.runtime_config import resolve_database_url


def uid(prefix): return prefix+'_'+uuid.uuid4().hex[:16]


class Store:
    def __init__(self, root, harness=None, database_url=None):
        self.root=Path(root); self.root.mkdir(parents=True,exist_ok=True)
        self.repo=Repository(resolve_database_url(harness,database_url))
        meta=MetaData()
        self.keys=Table('benchforge_request_keys',meta,Column('id',String,primary_key=True),Column('run_id',String))
        with self.repo.transaction() as conn:
            self.repo.lock_queue(conn)
            meta.create_all(conn)

    def submit(self,key,intents,workflow='scene',submitted_programs=None,context=None):
        if workflow == 'dataset':
            raise ValueError('Legacy dataset jobs are retired. Use data.capability or submit a scene with target_items.')
        from capabilities.scene_authoring.validation.acceptance import policy_for
        from capabilities.scene_authoring.validation.acceptance import check_references
        from capabilities.scene_authoring.validation.acceptance import admit_program
        external_context = RunContext.from_request(context)
        if external_context is None:
            external_context = RunContext()
        parent_runs = {
            str(intent.get(key)).split('.')[0]
            for intent in intents
            for key in ('parent_task_id', 'refine_task_id', 'extend_task_id')
            if intent.get(key)
        }
        if len(parent_runs) > 1:
            raise ValueError('one run cannot have multiple parent runs')
        inferred_parent = next(iter(parent_runs), None)
        if inferred_parent and external_context.parent_run_id not in (None, inferred_parent):
            raise ValueError('context.parent_run_id must match the scene lineage')
        if inferred_parent and not external_context.parent_run_id:
            external_context = RunContext(
                workspace_id=external_context.workspace_id,
                session_id=external_context.session_id,
                parent_run_id=inferred_parent,
            )
        scene_modes = [intent.get('execution_mode') for intent in intents] if workflow == 'scene' else []
        policies = {
            mode: policy_for(self.root, mode)
            for mode in set(scene_modes)
        }
        policy = next((value for value in policies.values() if value), {})
        if policy:
            check_references(intents,policy)
            requires_scene_programs = workflow == 'scene' and any(
                mode in (None, 'production') for mode in scene_modes
            )
            if requires_scene_programs:
                if not submitted_programs or len(submitted_programs)!=len(intents) or any(not row for row in submitted_programs):
                    raise ValueError('Realistic production requires complete SceneProgram snapshots; no planner fallback')
                for intent,submission in zip(intents,submitted_programs):
                    mode = intent.get('execution_mode')
                    admission=admit_program(self.root,submission['program'],allow_generated=True,execution_mode=mode)
                    lineage_task_id=intent.get('refine_task_id') or intent.get('extend_task_id')
                    if lineage_task_id:
                        from capabilities.scene_authoring.contracts.scene_design import require_prior_brief
                        require_prior_brief(admission.get('scene_brief') or {},
                            self.root/lineage_task_id/('revision_'+str(intent.get('refine_revision',intent.get('extend_revision')))))
        submitted_programs=submitted_programs or []
        if submitted_programs and (workflow!='scene' or len(submitted_programs)!=len(intents)):
            raise ValueError('SceneProgram snapshots must align with scene intents')
        with self.repo.transaction() as c:
            self.repo.lock_queue(c)
            old=c.execute(select(self.keys).where(self.keys.c.id==key)).mappings().first()
            if old:
                prior=c.execute(select(runs).where(runs.c.id==old['run_id'])).mappings().one()
                prior_context = prior['plan'].get('context') or {}
                context_matches = all(
                    prior_context.get(field) == getattr(external_context, field)
                    for field in ('workspace_id', 'session_id', 'parent_run_id')
                )
                previous_submissions = list(map(submission_identity, prior['plan'].get('submitted_programs', [])))
                if (prior['plan'].get('intents')!=intents or prior['plan'].get('workflow','scene')!=workflow
                        or previous_submissions != list(map(submission_identity, submitted_programs)) or not context_matches):
                    raise ValueError('request_key already belongs to a different request')
                return old['run_id']
            rid=uid('sf')
            run_context = external_context.for_run(rid)
            plan={'schema':'benchforge.pipeline/v1','intents':intents,'workflow':workflow,'created':time.time(),'code_version':'0.3.0'}
            if submitted_programs:plan['submitted_programs']=submitted_programs
            plan['context'] = run_context.to_dict()
            c.execute(runs.insert().values(id=rid,state='PENDING',plan=plan,created_at=str(time.time()),code_revision='benchforge-0.2.0'))
            for i,intent in enumerate(intents):
                tid=f'{rid}.scene{i}'
                unit={'intent':intent,'index':i,'workflow':workflow,'phase':workflow if workflow in {'asset','layout','generation'} else 'plan','revision':0}
                unit['run_context'] = run_context.to_dict()
                if submitted_programs and submitted_programs[i] is not None:unit['submitted_program']=submitted_programs[i]
                c.execute(tasks.insert().values(id=tid,run_id=rid,task_id=f'scene{i}',unit=unit,state='PENDING',attempt=0,ready_at=0))
            c.execute(self.keys.insert().values(id=key,run_id=rid))
            self.repo.event(c,rid,None,'benchforge.submitted',{'intents':intents})
            return rid

    def snapshot(self,rid):
        if isinstance(rid,str) and '.scene' in rid and rid.startswith('sf_'):
            raise ValueError(f'Expected run_id, received task_id {rid}; use run_id={rid.split(".")[0]} for status/wait/inspect/cancel, and the full task_id for evidence/retry')
        value=self.repo.status(rid)
        if value['plan'].get('schema') != 'benchforge.pipeline/v1':
            raise ValueError('not a BenchForge run')
        return value

    def update(self,tid,*,expected_state=None,expected_owner=None,**fields):
        with self.repo.transaction() as c:
            old=c.execute(select(tasks).where(tasks.c.id==tid).with_for_update()).mappings().one()
            run=c.execute(select(runs).where(runs.c.id==old['run_id'])).mappings().one()
            if expected_state is not None and old['state'] != expected_state:
                raise ValueError(f'task {tid} state changed before update')
            if expected_owner is not None and old['lease_owner'] != expected_owner:
                raise ValueError(f'task {tid} stage lease ownership changed')
            if expected_owner is not None and (old['lease_expires_at'] or 0) <= time.time():
                raise ValueError(f'task {tid} stage lease expired')
            if run['state']=='CANCELED' and fields.get('state')!='CANCELED':return
            if fields.get('state')=='SUCCEEDED' and old['unit'].get('workflow','scene')=='scene':
                from capabilities.scene_authoring.validation.acceptance import require_accepted
                require_accepted(
                    self.root,
                    self.directory(tid,old['unit']['revision']),
                    old['unit'].get('intent', {}).get('execution_mode'),
                )
            if 'unit' in fields:fields['unit']={**old['unit'],**fields['unit']}
            if fields.get('state') not in (None, 'RUNNING'):
                fields.setdefault('lease_owner', None)
                fields.setdefault('lease_expires_at', None)
            conditions = [tasks.c.id == tid, tasks.c.state == old['state']]
            if old['lease_owner'] is None:
                conditions.append(tasks.c.lease_owner.is_(None))
            else:
                conditions.append(tasks.c.lease_owner == old['lease_owner'])
            updated = c.execute(tasks.update().where(*conditions).values(**fields))
            if updated.rowcount != 1:
                raise ValueError(f'task {tid} changed before update')
            self.repo.event(c,old['run_id'],tid,'benchforge.transition',{'state':fields.get('state',old['state']),'phase':fields.get('unit',old['unit']).get('phase')})

    def claim_stage(self, tid, owner, lease_seconds=60):
        """Atomically claim or recover any pipeline stage for a single owner."""
        now = time.time()
        with self.repo.transaction() as c:
            task = c.execute(select(tasks).where(tasks.c.id == tid).with_for_update()).mappings().first()
            if not task:
                return None
            run = c.execute(select(runs).where(runs.c.id == task['run_id']).with_for_update()).mappings().one()
            if run['state'] == 'CANCELED' or (task.get('ready_at') or 0) > now:
                return None
            available = task['state'] in {'PENDING', 'READY'}
            expired = task['state'] == 'RUNNING' and (
                task.get('lease_expires_at') is None or task['lease_expires_at'] <= now
            )
            if not (available or expired):
                return None
            unit = dict(task['unit'])
            base_context = RunContext.from_record(run['plan'].get('context')) or RunContext()
            if not base_context.run_id:
                base_context = base_context.for_run(run['id'])
            unit['run_context'] = base_context.for_stage(tid, owner).to_dict()
            conditions = [tasks.c.id == tid, tasks.c.state == task['state']]
            if task.get('lease_owner') is None:
                conditions.append(tasks.c.lease_owner.is_(None))
            else:
                conditions.append(tasks.c.lease_owner == task['lease_owner'])
            if expired and task.get('lease_expires_at') is not None:
                conditions.append(tasks.c.lease_expires_at <= now)
            result = c.execute(
                tasks.update()
                .where(*conditions)
                .values(
                    state='RUNNING',
                    unit=unit,
                    lease_owner=owner,
                    lease_expires_at=now + lease_seconds,
                    failure=None,
                )
            )
            if result.rowcount != 1:
                return None
            claimed = c.execute(select(tasks).where(tasks.c.id == tid)).mappings().one()
            self.repo.event(
                c,
                task['run_id'],
                tid,
                'benchforge.stage_claimed',
                {'phase': unit.get('phase'), 'attempt_id': owner},
            )
            return dict(claimed)

    def renew_stage(self, tid, owner, lease_seconds=60):
        now = time.time()
        with self.repo.transaction() as c:
            result = c.execute(
                tasks.update()
                .where(
                    tasks.c.id == tid,
                    tasks.c.state == 'RUNNING',
                    tasks.c.lease_owner == owner,
                    tasks.c.lease_expires_at > now,
                )
                .values(lease_expires_at=now + lease_seconds)
            )
            return result.rowcount == 1

    def retry(self,tid,feedback=None,capture_mode='auto'):
        """Retain old attempts and retry the failed stage; never mutate successful products."""
        if capture_mode not in {'auto','resume','restart'}:
            raise ValueError('capture_mode must be auto, resume or restart')
        if feedback is not None and (not isinstance(feedback,str) or not 1<=len(feedback)<=12000):
            raise ValueError('retry feedback must be 1..12000 characters')
        with self.repo.transaction() as c:
            self.repo.lock_queue(c)
            task=c.execute(select(tasks).where(tasks.c.id==tid).with_for_update()).mappings().one()
            run=c.execute(select(runs).where(runs.c.id==task['run_id']).with_for_update()).mappings().one()
            if task['unit'].get('phase') == 'dataset':
                raise ValueError('Legacy dataset jobs are retired. Use data.capability or submit a scene with target_items.')
            if run['state'] in {'CANCELED','SUCCEEDED'}:raise ValueError('cannot retry a canceled or successful run')
            if task['state']!='FAILED_FINAL':raise ValueError('only failed tasks need retry')
            recovery_id=None
            from capabilities.scene_authoring.validation.acceptance import policy_for
            if policy_for(self.root) and task['unit'].get('workflow','scene')=='scene':
                from capabilities.scene_authoring.validation.acceptance import admit_program
                from capabilities.scene_authoring.contracts.scene_design import capture_retry_directory
                from capabilities.scene_authoring.contracts.scene_design import review_retry_directory
                mode = task['unit'].get('intent', {}).get('execution_mode')
                capture_failure=(task['unit']['phase']=='capture' and
                    (task.get('failure') or {}).get('stage') in {'capture_transport','capture_execution'} and feedback is None)
                review_failure=(task['unit']['phase']=='review' and feedback is None and
                    (task.get('failure') or {}).get('error') in {'TimeoutError','URLError','ConnectionError','OSError','RemoteDisconnected','ConnectionResetError','ConnectionAbortedError'})
                if not capture_failure and not review_failure:
                    raise ValueError('Only review transport or capture execution/transport failures can retry; repair scene quality through capture/refine')
                directory=(capture_retry_directory(self.root,tid,task['unit']['revision']) if capture_failure
                    else review_retry_directory(self.root,tid))
                admit_program(self.root,json.loads((directory/'program.json').read_text()),execution_mode=mode)
                if review_failure and task['unit'].get('review_transport_retries',0)>=2:
                    marker=self.root/'model_transport_recovery.json'
                    recovery=json.loads(marker.read_text()) if marker.is_file() and marker.stat().st_size<4096 else {}
                    recovery_id=recovery.get('recovery_id') if recovery.get('status')=='transport_verified' else None
                    if not isinstance(recovery_id,str) or not recovery_id or recovery_id==task['unit'].get('review_transport_recovery_id'):
                        raise ValueError('Review transport recovery budget exhausted')
            unit=dict(task['unit']);phase=unit['phase'];old=self.directory(tid,unit['revision'])
            if capture_mode!='auto' and not (phase=='capture' and (task.get('failure') or {}).get('stage') in {'capture_transport','capture_execution'}):
                raise ValueError('capture_mode applies only to capture execution/transport failures')
            if recovery_id:
                unit.update(review_transport_recovery_id=recovery_id,previous_review_transport_retries=unit.get('review_transport_retries',0),review_transport_retries=0)
            if policy_for(self.root) and phase=='review':unit['review_transport_retries']=unit.get('review_transport_retries',0)+1
            if phase=='capture' and (task.get('failure') or {}).get('stage') in {'capture_transport','capture_execution'}:
                from capabilities.isaac_sim.recovery import prepare_retry
                unit = prepare_retry(self.directory(tid, unit['revision']), unit, capture_mode,
                    retired_protocol=(task.get('failure') or {}).get('code') == 'CAPTURE_PROTOCOL_MIGRATED')
            if feedback is not None:
                if phase!='plan':raise ValueError('retry feedback requires a failed planning stage; use refine for captured scenes')
                unit['operator_feedback']=feedback
            if phase=='plan':
                # Failed structural proposals and model traces remain immutable.
                # A repaired planner must not replay cached invalid responses.
                if unit.get('quality_repairs'):
                    unit.setdefault('quality_source_revision',unit['revision']-1)
                if (old/'repair_seed.json').exists():
                    import shutil
                    shutil.copy2(old/'repair_seed.json',self.directory(tid,unit['revision']+1)/'repair_seed.json')
                else:
                    seeds=sorted(old.glob('model_calls/scene_plan_*/validation_error.json'),key=lambda p:int(p.parent.name.rsplit('_',1)[-1]),reverse=True)
                    for seed_path in seeds:
                        seed=json.loads(seed_path.read_text())
                        if isinstance(seed.get('proposal'),dict):
                            write_json(self.directory(tid,unit['revision']+1)/'repair_seed.json',seed);break
                unit.update(revision=unit['revision']+1,engineering_retry=True)
            if phase=='review' and (old/'capture'/'report.json').exists():
                report=json.loads((old/'capture'/'report.json').read_text())
                if report['status']!='captured':
                    unit.update(revision=unit['revision']+1,phase='capture',engineering_retry=True)
                    import shutil
                    shutil.copy2(old/'program.json',self.directory(tid,unit['revision'])/'program.json')
                    unit.pop('capture_token',None)
            c.execute(runs.update().where(runs.c.id==run['id']).values(state='PENDING'))
            c.execute(tasks.update().where(tasks.c.id==tid).values(state='READY',unit=unit,failure=None,result=None,ready_at=0,lease_owner=None,lease_expires_at=None))
            self.repo.event(c,run['id'],tid,'benchforge.retry',{'phase':unit['phase'],'revision':unit['revision'],'operator_feedback':feedback})
        return {'task_id':tid,'revision':unit['revision'],'phase':unit['phase'],
                **({'capture_recovery_mode':unit['capture_recovery_mode']} if phase=='capture' and 'capture_recovery_mode' in unit else {})}

    def all_work(self, connection=None):
        query=select(tasks).join(runs,tasks.c.run_id==runs.c.id).where(runs.c.code_revision=='benchforge-0.2.0',runs.c.state.notin_(['CANCELED','SUCCEEDED','FAILED_FINAL']))
        if connection is not None:
            return [dict(r) for r in connection.execute(query).mappings()]
        with self.repo.engine.connect() as c:
            return [dict(r) for r in c.execute(query).mappings()]

    def directory(self,tid,revision):
        directory=safe_path(self.root,f'{tid}/revision_{revision}')
        directory.mkdir(parents=True,exist_ok=True)
        return directory

    def publish_capture(self, tid, token, incoming, *, expected_owner):
        """Fence filesystem publication and stage transition with the task lease."""
        with self.repo.transaction() as connection:
            task = connection.execute(select(tasks).where(tasks.c.id == tid).with_for_update()).mappings().one()
            run = connection.execute(select(runs).where(runs.c.id == task['run_id']).with_for_update()).mappings().one()
            if (task['lease_owner'] != expected_owner or task['state'] != 'RUNNING'
                    or (task['lease_expires_at'] or 0) <= time.time() or run['state'] == 'CANCELED'):
                raise ValueError('Capture lease changed before publication; native receipts retained')
            report = json.loads((incoming / 'report.json').read_text())
            if report.get('token') != token or report.get('status') != 'captured':
                raise ValueError('Capture publication needs matching successful native evidence')
            destination = self.directory(tid, task['unit']['revision']) / 'capture'
            if destination.exists():
                if json.loads((destination / 'report.json').read_text()).get('token') != token:
                    raise ValueError('An immutable capture already exists')
            else:
                incoming.rename(destination)
            unit = {**task['unit'], 'phase': 'review', 'capture_token': token}
            connection.execute(tasks.update().where(tasks.c.id == tid).values(
                state='READY', unit=unit, lease_owner=None, lease_expires_at=None))
            self.repo.event(connection, task['run_id'], tid, 'benchforge.capture_committed',
                            {'report': report, 'protocol': 'a100sim.v1.Simulator'})

    def settle_runs(self):
        with self.repo.transaction() as c:
            for r in c.execute(select(runs).where(runs.c.code_revision=='benchforge-0.2.0')).mappings():
                if r['state'] in {'CANCELED','SUCCEEDED','FAILED_FINAL'}:continue
                work=list(c.execute(select(tasks).where(tasks.c.run_id==r['id'])).mappings())
                if all(w['state'] in {'SUCCEEDED','FAILED_FINAL','CANCELED'} for w in work):
                    state='SUCCEEDED' if all(w['state']=='SUCCEEDED' for w in work) else 'FAILED_FINAL'
                    c.execute(runs.update().where(runs.c.id==r['id']).values(state=state))
                elif any(w['state']=='RUNNING' for w in work) and r['state']!='RUNNING':
                    c.execute(runs.update().where(runs.c.id==r['id']).values(state='RUNNING'))

    def cancel(self,rid):
        self.snapshot(rid)
        with self.repo.transaction() as c:
            c.execute(runs.update().where(runs.c.id==rid).values(state='CANCELED'))
            c.execute(tasks.update().where(tasks.c.run_id == rid,
                tasks.c.state.in_(['PENDING', 'READY'])).values(state='CANCELED', lease_owner=None, lease_expires_at=None))
            self.repo.event(c,rid,None,'benchforge.cancel_requested')
