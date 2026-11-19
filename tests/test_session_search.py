"""PostgreSQL search isolation, filters, literal input and atomic replacement."""

from concurrent.futures import ThreadPoolExecutor
import unittest

from database.engine import engine_for
from storage.session_search import session_search
from tests.database_fixtures import use_postgres


class SessionSearchTests(unittest.TestCase):
    # 创建独立搜索数据库与固定宿主命名空间。
    def setUp(self):
        self.engine = engine_for(use_postgres(self))
        self.host = 'a' * 64

    # 将指定会话的测试文本完整替换为搜索文档，可覆盖宿主身份。
    def replace(self, session, texts, host=None):
        return session_search(self.engine, 'replace', {
            'host_id': host or self.host, 'session_id': session,
            'documents': [dict(seq=i, time=i, type='user/message', surface='current', text=text)
                          for i, text in enumerate(texts)],
        })

    # 为固定宿主及会话集合构造搜索请求，并允许测试覆盖过滤和分页参数。
    def query(self, **options):
        return session_search(self.engine, 'query', dict(
            host_id=self.host, sessions=['session-a', 'session-b'], query='场景', **options))

    # 验证搜索同时执行宿主与会话范围约束，隐藏其他主体的文本。
    def test_host_and_session_scope_are_enforced(self):
        self.replace('session-a', ['场景 A', '场景 B'])
        self.replace('session-secret', ['场景私有'])
        self.replace('session-b', ['场景其它主机'], host='b' * 64)
        self.assertEqual([row['seq'] for row in self.query()['items']], [1, 0])
        self.assertEqual(len(self.query(grouped=True)['items']), 1)

    # 验证序号、展示面等过滤边界及分页游标行为。
    def test_inclusive_filters_and_pagination(self):
        self.replace('session-a', ['场景一', '场景二', '场景三'])
        filters = [{'kind': 'seq', 'from': 1, 'to': 2}, {'kind': 'surface', 'values': ['current']}]
        first = self.query(filters=filters, limit=1)
        self.assertTrue(first['hasMore'])
        self.assertEqual(first['items'][0]['seq'], 2)
        second = self.query(filters=filters, limit=1, offset=1)
        self.assertFalse(second['hasMore'])
        self.assertEqual(second['items'][0]['seq'], 1)

    # 验证并发索引替换时读取只能看到完整旧版或新版。
    def test_replacement_is_atomic_for_concurrent_readers(self):
        self.replace('session-a', ['场景'] * 40)
        # 交替执行整批索引替换与条数读取，检测读到部分批次的情况。
        def operation(i):
            if i % 2:
                self.replace('session-a', ['场景新版'] * 60)
            else:
                return len(self.query(limit=100)['items'])
        with ThreadPoolExecutor(max_workers=8) as pool:
            counts = [n for n in pool.map(operation, range(24)) if n is not None]
        self.assertTrue(all(n in (40, 60) for n in counts), counts)

    # 验证查询特殊字符按字面处理，无效替换不破坏原数据。
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
