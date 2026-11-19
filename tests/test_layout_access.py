"""布局请求须兼容宿主附加的运行身份，同时保持领域合同和会话隔离。"""

from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from capabilities.asset_generation.layout import start_design_request
from database.access import AccessScope
from database.schema import request_keys
from server import http_api
from service_access.access_scope import TrustedAccess, access_context
from storage.scene_jobs.store import Store


class LayoutAccessTests(unittest.TestCase):
    def setUp(self):
        """准备真实存储提交逻辑，仅替换数据库连接与后台生成执行。"""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = object.__new__(Store)
        self.store.root = self.root
        self.store.lifecycle = MagicMock()
        self.store.repo = MagicMock()
        self.store.keys = request_keys
        self.connection = self.store.repo.transaction.return_value.__enter__.return_value
        self.connection.execute.return_value.mappings.return_value.first.return_value = None
        self.access = TrustedAccess(AccessScope('host', 'session-a', 'workspace-a', 'owner', 1, 1), str(self.root))
        self.request = {'request_key': 'dsh:session-a:call-layout', 'name': 'villa2f_drone_explore',
                        'description': '现代二层别墅室内斜俯视广角，黑色四旋翼无人机悬停楼梯上方'}

    def inserted(self):
        """读取真实存储层生成的插入参数，核对持久化身份与业务意图。"""
        return [call.args[0].compile().params for call in self.connection.execute.call_args_list
                if getattr(call.args[0], 'is_insert', False)]

    def test_layout_http_accepts_host_identity_without_changing_model_input(self):
        """走实际 HTTP 授权与分派，验证两会话隔离并拒绝未知业务字段。"""
        ready, holder = Event(), {}

        def server_factory(_address, handler):
            """把测试服务绑定到随机本机端口并通知请求端。"""
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
            holder['server'] = server
            ready.set()
            return server

        config = {'artifacts': str(self.root), 'operator_token': 'fixture-only', 'port': 0}
        with (
            patch('sys.argv', ['http_api', '--config', 'fixture.json']),
            patch('service_access.runtime_config.load_service_config', return_value=config),
            patch('service_access.runtime_config.apply_runtime_environment'),
            patch('storage.scene_jobs.migrate_capture.migrate_capture', return_value={}),
            patch.object(http_api, 'create_store', return_value=self.store),
            patch.object(http_api, 'coordinate'),
            patch.object(http_api, 'ThreadingHTTPServer', side_effect=server_factory),
            patch('service_access.access_scope.resolve_ticket') as resolve,
            patch('storage.access_identity.bind_run_scope') as bind,
            patch('storage.scene_jobs.store.lock_entity'),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            future = pool.submit(http_api.main)
            self.assertTrue(ready.wait(10))
            server = holder['server']

            def post(body):
                """发送宿主已签名的布局请求，不向业务输入暴露身份凭据。"""
                return urlopen(Request(
                    f'http://127.0.0.1:{server.server_address[1]}/layout',
                    data=json.dumps(body).encode(), headers={'Content-Type': 'application/json',
                        'Authorization': 'Bearer fixture-only', 'X-BenchForge-Scope': 'host-fixture'},
                ), timeout=10)

            try:
                keys = []
                for name in ('a', 'b'):
                    access = TrustedAccess(AccessScope('host', f'session-{name}', f'workspace-{name}',
                                                      'owner', 1, 1), str(self.root))
                    resolve.return_value = access
                    self.connection.execute.reset_mock()
                    # 模型伪造身份也不能覆盖 HTTP 层绑定的可信会话。
                    with post({**self.request, 'context': {'session_id': 'forged'}}) as response:
                        result = json.load(response)
                    self.assertEqual(result['state'], 'accepted')
                    inserted = self.inserted()
                    plan = next(row['plan'] for row in inserted if 'plan' in row)
                    unit = next(row['unit'] for row in inserted if 'unit' in row)
                    keys.append(next(row['id'] for row in inserted if 'run_id' in row and 'unit' not in row))
                    self.assertEqual(plan['context']['session_id'], access.scope.session_id)
                    self.assertEqual(plan['context']['workspace_id'], access.scope.workspace_id)
                    self.assertEqual(unit['run_context'], plan['context'])
                    self.assertEqual(set(plan['intents'][0]), {'name', 'description', 'layout'})
                    bind.assert_called_with(self.store.repo.engine, access.scope, result['run_id'])
                self.assertNotEqual(*keys)
                self.store.lifecycle.admit.reset_mock()
                with self.assertRaises(HTTPError) as denied:
                    post({**self.request, 'unexpected_layout_option': True})
                self.assertEqual(denied.exception.code, 400)
                denied.exception.close()
                self.store.lifecycle.admit.assert_not_called()
            finally:
                server.shutdown()
                future.result(timeout=10)

    def test_direct_layout_validates_context_and_preserves_operator_calls(self):
        """直调仍校验运行身份，未携带上下文的维护调用保持兼容。"""
        invalid = ({'run_id': 'forged'}, {'unexpected': 'field'}, {'session_id': 5}, 'session')
        for context in invalid:
            with self.subTest(context=context), self.assertRaises(ValueError):
                start_design_request(self.store, {**self.request, 'context': context})
        with access_context(self.access), self.assertRaises(PermissionError):
            start_design_request(self.store, {**self.request, 'context': {'session_id': 'other'}})
        self.store.lifecycle.admit.assert_not_called()
        with patch('storage.scene_jobs.store.lock_entity'):
            result = start_design_request(self.store, self.request)
        self.assertEqual(result['state'], 'accepted')
        self.assertNotIn('session_id', next(row['plan'] for row in self.inserted() if 'plan' in row)['context'])
