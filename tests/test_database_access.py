"""Access contracts that run locally without a database."""

import unittest
from unittest.mock import MagicMock
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

from database.access import AccessScope, identifier, set_scope
from database.access_policies import policy_expressions
from database.schema import metadata
from storage.access_queries import query_records


class AccessContractTests(unittest.TestCase):
    def setUp(self):
        self.scope = AccessScope("host-a", "session-a", "workspace-a", "owner-a", 1, 1)

    def test_scope_is_immutable_and_rejects_unknown_identity_syntax(self):
        with self.assertRaises(AttributeError):
            self.scope.workspace_id = "workspace-b"
        for value in ("../secret", "", "a\n", "x' OR true", None):
            with self.assertRaises(ValueError):
                identifier(value)
        with self.assertRaises(ValueError):
            AccessScope("h", "s", "w", "o", True, 1)

    def test_transaction_scope_uses_only_bound_values_and_local_settings(self):
        connection = MagicMock()
        set_scope(connection, self.scope)
        self.assertEqual(connection.execute.call_count, 7)
        for call in connection.execute.call_args_list:
            self.assertIn("set_config(:name, :value, true)", str(call.args[0]))
            self.assertTrue(call.args[1]["name"].startswith("benchforge."))
        with self.assertRaises(TypeError):
            set_scope(connection, {"workspace_id": "forged"})

    def test_fixed_queries_reject_sql_and_unbounded_pages_before_connecting(self):
        engine = MagicMock()
        for resource in ("SELECT * FROM reviews", "schema_versions", "all"):
            with self.assertRaises(ValueError):
                query_records(engine, self.scope, resource)
        for limit in (-1, 0, 101, True):
            with self.assertRaises(ValueError):
                query_records(engine, self.scope, "runs", limit=limit)
        engine.connect.assert_not_called()

    def test_schema_has_no_create_time_cycles_and_identity_constraints(self):
        for table in metadata.sorted_tables:
            self.assertTrue(str(CreateTable(table).compile(dialect=postgresql.dialect())))
        self.assertEqual(len(metadata.tables), 19)
        self.assertTrue(metadata.tables["workspace_sessions"].foreign_key_constraints)
        self.assertTrue(metadata.tables["asset_versions"].foreign_key_constraints)

    def test_rls_has_no_unscoped_private_read_policy(self):
        policies = policy_expressions()
        self.assertNotIn("legacy_review_ids", policies)
        self.assertNotIn("benchforge_migrations", policies)
        self.assertIn("policy_epoch", policies["workspaces"])
        self.assertIn("session_id", policies["runs"])
        self.assertIn("revoked_at IS NULL", policies["asset_grants"])


if __name__ == "__main__":
    unittest.main()
