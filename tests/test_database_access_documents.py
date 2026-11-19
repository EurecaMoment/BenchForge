"""Authoritative document shape and optimistic concurrency contracts."""

import unittest
from unittest.mock import MagicMock
from database.access import AccessScope
from storage.access_documents import encode_document, write_document


class DocumentValidationTests(unittest.TestCase):
    # 验证文档摘要不受键顺序影响，并拒绝 NaN 等非 JSON 数值。
    def test_content_hash_is_canonical_and_does_not_accept_non_json_numbers(self):
        left = encode_document({"state": "RUNNING", "revision": 1})
        right = encode_document({"revision": 1, "state": "RUNNING"})
        self.assertEqual(left, right)
        with self.assertRaises(ValueError):
            encode_document({"score": float("nan")})
        with self.assertRaises(ValueError):
            encode_document(["not", "a", "document"])

    # 验证非法版本及文档名称在数据库连接前被拒绝。
    def test_invalid_revision_and_names_never_open_database(self):
        scope = AccessScope("h", "s", "w", "o", 1, 1)
        engine = MagicMock()
        for name in ("../state.json", "/state.json", "steps/../state.json"):
            with self.assertRaises(ValueError):
                write_document(engine, scope, "run", name, {}, expected_revision=0)
        with self.assertRaises(ValueError):
            write_document(engine, scope, "run", "state.json", {}, expected_revision=True)
        engine.begin.assert_not_called()
