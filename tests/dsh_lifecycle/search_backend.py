"""Authenticated production search routes on a disposable PostgreSQL schema."""

from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import signal
import sys
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from database.engine import engine_for
from server import http_api
from tests.database_fixtures import use_postgres


# 在独立测试数据库上启动搜索后端，写出临时连接配置并处理退出清理。
def main():
    case = unittest.TestCase()
    try:
        engine = engine_for(use_postgres(case))
        destination = Path(sys.argv[1])
        config = {'artifacts': str(destination.parent / 'search-artifacts'),
                  'operator_token': 'isolated-search-fixture', 'port': 0}
        store = SimpleNamespace(root=Path(config['artifacts']), repo=SimpleNamespace(engine=engine))
        # 将测试 HTTP 服务绑定到随机本机端口，并把实际地址写给生命周期测试。
        def factory(_address, handler):
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
            destination.write_text(json.dumps({
                'execution_mode': 'exploration', 'operator_token': config['operator_token'],
                'service_url': 'http://127.0.0.1:' + str(server.server_address[1]),
            }), encoding='utf8')
            return server
        # 把进程停止信号转换为中断，使测试后端正常进入清理路径。
        def stopped(_signum, _frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stopped)
        with (
            patch('sys.argv', ['http_api', '--config', 'isolated-search.json']),
            patch('service_access.runtime_config.load_service_config', return_value=config),
            patch('service_access.runtime_config.apply_runtime_environment'),
            patch('storage.scene_jobs.migrate_capture.migrate_capture', return_value={}),
            patch.object(http_api, 'create_store', return_value=store),
            patch.object(http_api, 'coordinate', side_effect=lambda *a, **k: Event().wait(1)),
            patch.object(http_api, 'ThreadingHTTPServer', side_effect=factory),
        ):
            http_api.main()
    except KeyboardInterrupt:
        pass
    finally:
        case.doCleanups()


if __name__ == '__main__':
    main()
