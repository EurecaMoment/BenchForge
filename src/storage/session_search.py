"""Host-scoped, rebuildable DSH search documents in PostgreSQL."""

import math
import re
from sqlalchemy import select, func
from database.schema import session_documents as documents
from database.transactions import lock_entity
from database.migrations import initialize_schema


# 按宿主命名空间处理会话搜索索引的替换、删除或检索，并校验输入边界。
def session_search(engine, action, value):
    host = value.get('host_id')
    if not isinstance(host, str) or not re.fullmatch(r'[a-f0-9]{64}', host):
        raise ValueError('Invalid host namespace')
    initialize_schema(engine)
    if action == 'replace':
        session = value.get('session_id')
        rows = value.get('documents')
        if not isinstance(session, str) or not session.startswith('session-') or not isinstance(rows, list):
            raise ValueError('Invalid session search records')
        values = []
        for row in rows:
            if (not isinstance(row, dict) or type(row.get('seq')) is not int or row['seq'] < 0
                    or not isinstance(row.get('time'), (int, float))
                    or not math.isfinite(row['time'])
                    or not all(isinstance(row.get(key), str) for key in ('type', 'surface', 'text'))):
                raise ValueError('Invalid session search document')
            values.append(dict(host_id=host, session_id=session,
                               **{key: row[key] for key in ('seq', 'time', 'type', 'surface', 'text')}))
        with engine.begin() as connection:
            lock_entity(connection, 'session-search', host, session)
            connection.execute(documents.delete().where(documents.c.host_id == host, documents.c.session_id == session))
            for start in range(0, len(values), 200):
                connection.execute(documents.insert(), values[start:start+200])
        return {'indexed': len(values)}
    if action != 'query':
        raise ValueError('Unknown session search action')
    query, sessions = value.get('query'), value.get('sessions')
    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        raise ValueError('Search query must contain 1..500 characters')
    if not isinstance(sessions, list) or len(sessions) > 5000 or not all(isinstance(s, str) for s in sessions):
        raise ValueError('Invalid session scope')
    limit, offset = value.get('limit', 30), value.get('offset', 0)
    if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or not 0 <= offset <= 1000000:
        raise ValueError('Invalid search page')
    clauses = [documents.c.host_id == host, documents.c.session_id.in_(sessions)]
    for word in query.lower().split():
        clauses.append(func.strpos(func.lower(documents.c.text), word) > 0)
    for item in value.get('filters', []):
        kind = item.get('kind')
        if kind in ('type', 'surface'):
            clauses.append(documents.c[kind].in_(item['values']))
        elif kind in ('seq', 'time'):
            for field, compare in [('from', 'ge'), ('to', 'le')]:
                if field in item:
                    if not isinstance(item[field], (int, float)) or not math.isfinite(item[field]):
                        raise ValueError('Invalid search range')
                    clauses.append(documents.c[kind] >= item[field] if compare == 'ge' else documents.c[kind] <= item[field])
        else:
            raise ValueError('Invalid search filter')
    # Rank recent matching messages deterministically; literal text never becomes SQL syntax.
    query_rows = select(documents).where(*clauses).order_by(documents.c.time.desc(), documents.c.session_id, documents.c.seq.desc())
    if value.get('grouped'):
        matched = query_rows.subquery()
        ranked = select(matched, func.row_number().over(partition_by=matched.c.session_id,
                        order_by=(matched.c.time.desc(), matched.c.seq.desc())).label('rank')).subquery()
        query_rows = select(ranked).where(ranked.c.rank == 1).order_by(ranked.c.time.desc(), ranked.c.session_id)
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as connection:
        connection.exec_driver_sql('SET TRANSACTION READ ONLY')
        rows = connection.execute(query_rows.offset(offset).limit(limit+1)).mappings().all()
    hits = []
    for row in rows[:limit]:
        start = max(0, row['text'].lower().find(query.lower().split()[0]) - 80)
        hits.append({'sessionId': row['session_id'], 'seq':row['seq'], 'type':row['type'],
                     'surface':row['surface'], 'time':row['time'], 'snippet':row['text'][start:start+320]})
    return {'items': hits, 'hasMore':len(rows)>limit}
