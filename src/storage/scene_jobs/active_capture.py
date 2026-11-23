"""Identify concurrent submissions of the same frozen production capture."""
import hashlib
import json

from sqlalchemy import select

from database.schema import runs, work_items
from database.transactions import lock_entity


def capture_identity(intents, submissions, design):
    """以执行输入和冻结设计识别直接采集，同一场景换标题或复制 JSON 仍属同一输入。"""
    if design is None or len(intents) != 1 or not submissions or not submissions[0]:
        return None
    intent = intents[0]
    if (not intent.get('capture_only') or intent.get('execution_mode') != 'production'
            or any(intent.get(k) for k in ('parent_task_id','refine_task_id','extend_task_id'))
            or intent.get('layout', {}).get('mode') in ('generate','edit')):
        return None
    settings = {k:v for k,v in intent.items() if k not in ('name','scene_program_path')}
    payload = json.dumps([submissions[0]['program'], design, settings], sort_keys=True,
                         separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def find_active_capture(connection, scope, identity):
    """并发提交共享同一事务锁；只复用本工作区、本会话中仍在执行的采集。"""
    lock_entity(connection, 'active-capture', scope.workspace_id, scope.session_id, identity)
    return connection.execute(select(runs.c.id).join(work_items,work_items.c.run_id == runs.c.id).where(
        runs.c.workspace_id == scope.workspace_id, runs.c.session_id == scope.session_id,
        runs.c.plan['capture_identity'].as_string() == identity,
        runs.c.state.in_(('PENDING','RUNNING')), work_items.c.state.in_(('PENDING','READY','RUNNING')),
    ).limit(1)).scalar_one_or_none()
