"""Exercise actual preflight/submission HTTP responses without a model or GPU."""
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import http_api
from test_scene_diagnostics import sample, object_record


class PreflightHTTPTests(unittest.TestCase):
    # 验证 HTTP 诊断和提交均报告完整批量错误，失败候选不入队。
    def test_diagnostics_and_submission_report_all_failures_without_enqueueing(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / 'operator_workspaces' / 'http'
            workspace.mkdir(parents=True)
            source = workspace / 'scene.json'
            program = sample()
            program['objects'] = [object_record(f'item_{i}', support=f'absent_{i}',
                                                 cast_shadows=False) for i in range(40)]
            source.write_text(json.dumps(program), encoding='utf-8')
            store = SimpleNamespace(root=root, submit=Mock())
            ready, holder = Event(), {}

            # 绑定随机本机端口的测试服务并通知客户端已就绪。
            def server_factory(_address, handler):
                server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
                holder['server'] = server
                ready.set()
                return server

            config = {'artifacts': str(root), 'operator_token': 'fixture-only', 'port': 0}
            with (
                patch('sys.argv', ['http_api', '--config', 'fixture.json']),
                patch('service_access.runtime_config.load_service_config', return_value=config),
                patch('service_access.runtime_config.apply_runtime_environment'),
                patch('storage.scene_jobs.migrate_capture.migrate_capture', return_value={}),
                patch.object(http_api, 'create_store', return_value=store),
                patch.object(http_api, 'coordinate'),
                patch.object(http_api, 'ThreadingHTTPServer', side_effect=server_factory),
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                future = pool.submit(http_api.main)
                self.assertTrue(ready.wait(10))
                server = holder['server']
                base = 'http://127.0.0.1:' + str(server.server_address[1])

                # 向测试路由发送带可选授权的 JSON，覆盖认证与完整诊断响应。
                def request(route, authorized=True):
                    headers = {'Content-Type': 'application/json'}
                    if authorized:
                        headers['Authorization'] = 'Bearer fixture-only'
                    # Authentication rejects before reading a body. Keep this probe empty
                    # so Windows does not reset a connection with unread request bytes.
                    body = json.dumps({
                        'scene_program_path': str(source), 'execution_mode': 'exploration',
                        'request_key': 'fixture-request',
                    }).encode() if authorized else b''
                    return Request(base + route, headers=headers, data=body)

                try:
                    with self.assertRaises(HTTPError) as denied:
                        urlopen(request('/scene-preflight', False), timeout=5)
                    self.assertEqual(denied.exception.code, 401)
                    denied.exception.close()
                    with urlopen(request('/scene-preflight'), timeout=10) as response:
                        report = json.load(response)
                    self.assertFalse(report['passed'])
                    self.assertEqual(report['summary']['errors'], 80)
                    self.assertEqual(json.loads(Path(report['report_path']).read_text(encoding='utf-8')), report)
                    with self.assertRaises(HTTPError) as rejected:
                        urlopen(request('/capture'), timeout=10)
                    self.assertEqual(rejected.exception.code, 400)
                    payload = json.load(rejected.exception)
                    rejected.exception.close()
                    self.assertEqual(payload['code'], 'scene_preflight_failed')
                    self.assertEqual(payload['preflight']['issues'], report['issues'])
                    self.assertNotEqual(payload['preflight']['report_path'], report['report_path'])
                    store.submit.assert_not_called()
                    # 有效预览复用预检几何，仍保持原响应，不再构造生成器重复读取网格。
                    from capabilities.scene_authoring.geometry.preview import preview_scene_geometry
                    source.write_text(json.dumps(sample()), encoding='utf-8')
                    with patch('capabilities.scene_authoring.validation.preflight.preview_scene_geometry',
                               wraps=preview_scene_geometry) as geometry:
                        with urlopen(request('/preview'), timeout=10) as response:
                            preview = json.load(response)
                    self.assertEqual(geometry.call_count, 1)
                    self.assertEqual(preview, preview_scene_geometry(sample()))
                finally:
                    server.shutdown()
                    future.result(timeout=10)
