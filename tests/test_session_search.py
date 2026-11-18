"""PostgreSQL search isolation, filters, literal input and atomic replacement."""

from concurrent.futures import ThreadPoolExecutor
import unittest

from database.engine import engine_for
from storage.session_search import session_search
from tests.database_fixtures import use_postgres


class SessionSearchTests(unittest.TestCase):
    def setUp(self):
        self.engine = engine_for(use_postgres(self))
        self.host = 'a' * 64

    def replace(self, session, texts, host=None):
        return session_search(self.engine, 'replace', {
            'host_id': host or self.host, 'session_id': session,
            'documents': [dict(seq=i, time=i, type='user/message', surface='current', text=text)
                          for i, text in enumerate(texts)],
        })

    def query(self, **options):
        return session_search(self.engine, 'query', dict(
            host_id=self.host, sessions=['session-a', 'session-b'], query='场景', **options))

    def test_host_and_session_scope_are_enforced(self):
        self.replace('session-a', ['场景 A', '场景 B'])
        self.replace('session-secret', ['场景私有'])
        self.replace('session-b', ['场景其它主机'], host='b' * 64)
        self.assertEqual([row['seq'] for row in self.query()['items']], [1, 0])
        self.assertEqual(len(self.query(grouped=True)['items']), 1)

    def test_inclusive_filters_and_pagination(self):
        self.replace('session-a', ['场景一', '场景二', '场景三'])
        filters = [{'kind': 'seq', 'from': 1, 'to': 2}, {'kind': 'surface', 'values': ['current']}]
        first = self.query(filters=filters, limit=1)
        self.assertTrue(first['hasMore'])
        self.assertEqual(first['items'][0]['seq'], 2)
        second = self.query(filters=filters, limit=1, offset=1)
        self.assertFalse(second['hasMore'])
        self.assertEqual(second['items'][0]['seq'], 1)

    def test_replacement_is_atomic_for_concurrent_readers(self):
        self.replace('session-a', ['场景'] * 40)
        def operation(i):
            if i % 2:
                self.replace('session-a', ['场景新版'] * 60)
            else:
                return len(self.query(limit=100)['items'])
        with ThreadPoolExecutor(max_workers=8) as pool:
            counts = [n for n in pool.map(operation, range(24)) if n is not None]
        self.assertTrue(all(n in (40, 60) for n in counts), counts)

    def test_literals_and_invalid_replace_preserve_existing_data(self):
        self.replace('session-a', ["场景 ' OR 1=1 -- %_", '普通文字'])
        result = session_search(self.engine, 'query', {
            'host_id': self.host, 'sessions': ['session-a'], 'query': '%_',
        })
        self.assertEqual(len(result['items']), 1)
        with self.assertRaises(ValueError):
            session_search(self.engine, 'replace', {
                'host_id': self.host, 'session_id': 'session-a', 'documents': [None],
            })
        self.assertEqual(len(self.query()['items']), 1)
