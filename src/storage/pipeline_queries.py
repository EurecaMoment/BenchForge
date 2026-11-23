"""Bounded Host queries of committed pipeline state, never workspace projections."""

import os
from pathlib import Path

from sqlalchemy import literal_column, select
from sqlalchemy.dialects.postgresql import JSONB

from database.config import resolve_workspace_url
from database.engine import engine_for
from database.schema import pipeline_documents, workspaces
from storage.access_documents import run_document_key
from storage.access_identity import workspace_namespace


def _summary_document():
    """在 PostgreSQL 内裁出进度字段，不把步骤的大型结果或计划输入传到查询进程。"""
    return literal_column("""
        CASE WHEN pipeline_documents.name = 'plan.json' THEN
          jsonb_build_object('steps', COALESCE((
            SELECT jsonb_agg(jsonb_strip_nulls(jsonb_build_object(
              'id', item->'id', 'uses', item->'uses', 'review_stage', item->'review_stage',
              'await_terminal', item->'await_terminal',
              'arguments', jsonb_build_object('run_id', item#>'{arguments,run_id}'))))
            FROM jsonb_array_elements(CASE WHEN jsonb_typeof(document->'steps') = 'array'
              THEN document->'steps' ELSE '[]'::jsonb END) AS item
          ), '[]'::jsonb))
        ELSE jsonb_strip_nulls(jsonb_build_object(
          'state', document->'state', 'run_id', document->'run_id', 'step', document->'step',
          'next', document->'next', 'missing_binding', document->'missing_binding',
          'stage', document->'stage', 'error', document->'error',
          'run_path', document->'run_path', 'stage_review', document->'stage_review',
          'steps', COALESCE((
            SELECT jsonb_object_agg(step.key, jsonb_strip_nulls(jsonb_build_object(
              'state', step.value->'state', 'error', step.value->'error',
              'result', jsonb_strip_nulls(jsonb_build_object(
                'run_id', step.value#>'{result,run_id}', 'dataset', step.value#>'{result,dataset}',
                'schema', step.value#>'{result,schema}', 'state', step.value#>'{result,state}',
                'items', step.value#>'{result,items}', 'required_items', step.value#>'{result,required_items}',
                'annotations', jsonb_build_object('items', step.value#>'{result,annotations,items}'),
                'collection', jsonb_build_object(
                  'records', step.value#>'{result,collection,records}', 'scenes', step.value#>'{result,collection,scenes}',
                  'media_files', step.value#>'{result,collection,media_files}',
                  'native_episodes', step.value#>'{result,collection,native_episodes}',
                  'native_samples', step.value#>'{result,collection,native_samples}'),
                'steps', COALESCE((
                  SELECT jsonb_object_agg(child.key, jsonb_build_object('result',
                    jsonb_build_object('dataset', child.value#>'{result,dataset}')))
                  FROM jsonb_each(CASE WHEN jsonb_typeof(step.value#>'{result,steps}') = 'object'
                    THEN step.value#>'{result,steps}' ELSE '{}'::jsonb END) AS child
                  WHERE jsonb_typeof(child.value#>'{result,dataset}') = 'string'
                ), '{}'::jsonb)
              ))
            ))) FROM jsonb_each(CASE WHEN jsonb_typeof(document->'steps') = 'object'
              THEN document->'steps' ELSE '{}'::jsonb END) AS step
          ), '{}'::jsonb)
        )) END
    """, type_=JSONB)


def query_pipeline_runs(workspace, runs, *, sessions=(), history=False, database_url=None, view='summary'):
    """按宿主确认的工作区和会话批量读取计划与状态；副本缺失或被改动不影响结果。"""
    if not isinstance(runs, list) or len(runs) > 201:
        raise ValueError('Pipeline query accepts at most 201 run paths')
    if view not in ('summary', 'detail') or view == 'detail' and len(runs) != 1:
        raise ValueError('Pipeline detail requires exactly one run; batches use summary')
    if not isinstance(sessions, (list, tuple)) or any(not isinstance(s, str) or not s for s in sessions):
        raise ValueError('Session scope must contain nonempty IDs')
    if not history and not sessions:
        return []
    root = Path(workspace).resolve()
    identities = {}
    for value in runs:
        if not isinstance(value, str) or not value or len(value) > 4096:
            raise ValueError('Pipeline run must be a bounded path')
        directory = (root / value).resolve()
        if not directory.is_relative_to(root) or directory == root:
            raise PermissionError('Pipeline run is outside the registered workspace')
        identities[run_document_key(directory)] = str(directory)
    if not identities:
        return []
    engine = engine_for(resolve_workspace_url(root, database_url))
    namespace = workspace_namespace(engine, root)
    clauses = [pipeline_documents.c.workspace_id == namespace,
               workspaces.c.root_path == os.path.normcase(str(root)), workspaces.c.state == 'active',
               pipeline_documents.c.run_id.in_(identities),
               pipeline_documents.c.name.in_(('state.json', 'plan.json'))]
    if not history:
        clauses.append(pipeline_documents.c.session_id.in_(sessions))
    document = pipeline_documents.c.document if view == 'detail' else _summary_document()
    query = select(pipeline_documents.c.run_id, pipeline_documents.c.session_id,
                   pipeline_documents.c.name, pipeline_documents.c.revision,
                   pipeline_documents.c.updated_at, document.label('document')).join(workspaces,
        workspaces.c.workspace_id == pipeline_documents.c.workspace_id).where(*clauses)
    records = {}
    with engine.connect() as connection, connection.begin():
        connection.exec_driver_sql('SET TRANSACTION READ ONLY')
        connection.exec_driver_sql("SET LOCAL statement_timeout = '5000ms'")
        for row in connection.execute(query).mappings():
            key = row['run_id']
            record = records.setdefault(key, {'run': identities[key], 'session_id': row['session_id'],
                                               'authority': 'postgresql', 'revisions': {}})
            if record['session_id'] != row['session_id']:
                raise PermissionError('Pipeline run has ambiguous session ownership')
            name = row['name'].removesuffix('.json')
            record[name] = row['document']
            record['revisions'][name] = row['revision']
            if name == 'state':
                record['updated_at'] = row['updated_at'].isoformat()
    return [record for record in records.values() if 'state' in record]


def latest_production_execution(workspace, session):
    """读取同会话最近的顶层生产执行，排除嵌套运行和可被编辑的文件投影。"""
    root = Path(workspace).resolve()
    engine = engine_for(resolve_workspace_url(root))
    namespace = workspace_namespace(engine, root, session_id=session)
    plans = select(pipeline_documents.c.workspace_id, pipeline_documents.c.session_id,
                   pipeline_documents.c.run_id,
                   pipeline_documents.c.document['stage_review_key'].as_string().label('plan_key'),
                   pipeline_documents.c.document['steps'].label('steps')).where(
                       pipeline_documents.c.name == 'plan.json',
                       pipeline_documents.c.document['production_contract'].as_string() ==
                       'benchforge.production-stages/v1').subquery('plans')
    query = select(pipeline_documents.c.run_id, _summary_document().label('state'),
                   plans.c.plan_key, plans.c.steps).join(plans,
        (plans.c.workspace_id == pipeline_documents.c.workspace_id) &
        (plans.c.session_id == pipeline_documents.c.session_id) &
        (plans.c.run_id == pipeline_documents.c.run_id))
    query = query.join(workspaces, workspaces.c.workspace_id == pipeline_documents.c.workspace_id)
    query = query.where(pipeline_documents.c.workspace_id == namespace,
                        pipeline_documents.c.session_id == session,
                        workspaces.c.root_path == os.path.normcase(str(root)), workspaces.c.state == 'active',
                        pipeline_documents.c.name == 'state.json')
    with engine.connect() as connection, connection.begin():
        connection.exec_driver_sql('SET TRANSACTION READ ONLY')
        connection.exec_driver_sql("SET LOCAL statement_timeout = '5000ms'")
        row = connection.execute(query.order_by(pipeline_documents.c.updated_at.desc(),
                                                pipeline_documents.c.run_id).limit(1)).mappings().first()
    if not row:
        return None
    result = dict(row)
    candidates = [result['state'].get('run_path')]
    if result['plan_key']:
        candidates.append(str(Path(result['plan_key']).parent / 'pipeline_run'))
    # 兼容早期按操作目录存放的运行；路径由数据库操作索引提供，不扫描任务文件。
    def matched(paths):
        """仅采用当前工作区内、摘要与数据库运行身份一致的路径。"""
        return next((str(Path(path).resolve()) for path in paths if path
                         and Path(path).resolve().is_relative_to(root)
                         and run_document_key(path) == result['run_id']), '')
    result['run'] = matched(candidates)
    if not result['run']:
        from storage.operation_index import query_operations
        result['run'] = matched(str(Path(item['artifact']) / 'pipeline_run') for item in
                               query_operations(root, sessions=[session], tools=['pipeline_run'], limit=201))
    return result
