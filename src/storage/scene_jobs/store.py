"""Persist scene production and capture state in the BenchForge database."""
import json
from pathlib import Path
import time
import uuid
from sqlalchemy import Table
from sqlalchemy import Column
from sqlalchemy import String
from sqlalchemy import MetaData
from sqlalchemy import select
from storage.scene_jobs.metadata import Repository
from storage.scene_jobs.metadata import runs
from storage.scene_jobs.metadata import tasks
from storage.files import safe_path
from service_access.request_context import RunContext
from service_access.runtime_config import resolve_database_url


def uid(prefix): return prefix+'_'+uuid.uuid4().hex[:16]


class Store:
    def __init__(self, root, harness=None, database_url=None, *, lifecycle):
        self.lifecycle = lifecycle
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
        self.lifecycle.admit(intents, workflow, submitted_programs)
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
                previous_submissions = list(map(self.lifecycle.submission_identity, prior['plan'].get('submitted_programs', [])))
                if (prior['plan'].get('intents')!=intents or prior['plan'].get('workflow','scene')!=workflow
                        or previous_submissions != list(map(self.lifecycle.submission_identity, submitted_programs)) or not context_matches):
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
            if fields.get('state') == 'SUCCEEDED':
                self.lifecycle.require_success(self.directory(tid, old['unit']['revision']), old['unit'])
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
            unit = self.lifecycle.retry(task, feedback, capture_mode, self.directory)
            phase = task['unit']['phase']
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
