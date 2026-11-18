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


def main():
    case = unittest.TestCase()
    try:
        engine = engine_for(use_postgres(case))
        destination = Path(sys.argv[1])
        config = {'artifacts': str(destination.parent / 'search-artifacts'),
                  'operator_token': 'isolated-search-fixture', 'port': 0}
        store = SimpleNamespace(root=Path(config['artifacts']), repo=SimpleNamespace(engine=engine))
        def factory(_address, handler):
            server = ThreadingHTTPServer(('127.0.0.1', 0), handler)
            destination.write_text(json.dumps({
                'execution_mode': 'exploration', 'operator_token': config['operator_token'],
                'service_url': 'http://127.0.0.1:' + str(server.server_address[1]),
            }), encoding='utf8')
            return server
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
