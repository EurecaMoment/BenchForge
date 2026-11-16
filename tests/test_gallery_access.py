"""Exercise real HTTP routes with an isolated fixture store and no simulator."""

from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import unittest
from unittest.mock import patch

from server import http_api


class GalleryAccessTests(unittest.TestCase):
    def test_image_and_gallery_require_the_same_credential_as_post(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "unregistered.png").write_bytes(b"png")
            ready, holder = Event(), {}

            def server_factory(address, handler):
                server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
                holder["server"] = server
                ready.set()
                return server

            config = {"artifacts": str(root), "operator_token": "fixture-only", "port": 0}
            with (
                patch("sys.argv", ["http_api", "--config", str(root / "config.json")]),
                patch("service_access.runtime_config.load_service_config", return_value=config),
                patch("service_access.runtime_config.apply_runtime_environment"),
                patch("storage.scene_jobs.migrate_capture.migrate_capture", return_value={}),
                patch.object(http_api, "create_store"),
                patch.object(http_api, "coordinate"),
                patch.object(http_api, "ThreadingHTTPServer", side_effect=server_factory),
                ThreadPoolExecutor(max_workers=1) as pool,
            ):
                future = pool.submit(http_api.main)
                self.assertTrue(ready.wait(10))
                server = holder["server"]
                base = "http://127.0.0.1:" + str(server.server_port)
                try:
                    for route in ("/image/unregistered.png", "/gallery/sf_fixture"):
                        with self.assertRaises(HTTPError) as rejected:
                            urlopen(base + route, timeout=5)
                        self.assertEqual(rejected.exception.code, 401)
                        rejected.exception.close()
                    with urlopen(
                        Request(
                            base + "/image/unregistered.png",
                            headers={"Authorization": "Bearer fixture-only"},
                        ),
                        timeout=5,
                    ) as response:
                        self.assertEqual(response.read(), b"png")
                    with self.assertRaises(HTTPError) as rejected:
                        urlopen(
                            Request(
                                base + "/catalog",
                                data=b"{}",
                                headers={"Content-Type": "application/json"},
                            ),
                            timeout=5,
                        )
                    self.assertEqual(rejected.exception.code, 401)
                    rejected.exception.close()
                finally:
                    server.shutdown()
                    future.result(timeout=10)
