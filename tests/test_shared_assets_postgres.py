"""Version/grant semantics on PostgreSQL; storage file verification is service-owned."""

from pathlib import Path
import tempfile
import unittest

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from tests.database_fixtures import use_postgres
from database.engine import engine_for
from database.schema import asset_versions
from storage.access_identity import register_session_workspace
from storage.shared_assets import publish_version, grant_version, revoke_grant, read_version, list_versions


class SharedAssetPostgresTests(unittest.TestCase):
    def setUp(self):
        self.engine = engine_for(use_postgres(self))
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.scopes = []
        for name in ("a", "b"):
            root = self.root / name
            root.mkdir()
            self.scopes.append(register_session_workspace(self.engine, host_id="host", session_id="session-" + name,
                owner_id="owner", root_path=root))
        self.files = [{"path": "mesh.glb", "sha256": "a" * 64, "bytes": 100}]

    def publish(self, scope, **extra):
        return publish_version(self.engine, scope, name="object", kind="mesh", manifest=self.files,
                               storage_ref="verified/mesh", **extra)

    def test_versions_immutable_grants_explicit_and_revocable(self):
        a, b = self.scopes
        one = self.publish(a)
        duplicate = self.publish(b)
        self.assertEqual(one["content_hash"], duplicate["content_hash"])
        with self.assertRaises(PermissionError):
            read_version(self.engine, b, one["asset_version_id"], enforce_role=False)
        grant = grant_version(self.engine, a, one["asset_version_id"], grantee_workspace_id=b.workspace_id)
        result = read_version(self.engine, b, one["asset_version_id"], enforce_role=False)
        self.assertNotIn("storage_ref", result)
        with self.assertRaises(PermissionError):
            self.publish(b, derived_from_version_id=one["asset_version_id"])
        grant_version(self.engine, a, one["asset_version_id"], grantee_workspace_id=b.workspace_id, allow_derive=True)
        self.assertTrue(self.publish(b, derived_from_version_id=one["asset_version_id"])["asset_id"])
        two = self.publish(a, asset_id=one["asset_id"])
        self.assertEqual(two["version"], 2)
        with self.assertRaises(PermissionError):
            read_version(self.engine, b, two["asset_version_id"], enforce_role=False)
        revoke_grant(self.engine, a, grant["grant_id"])
        with self.assertRaises(PermissionError):
            read_version(self.engine, b, one["asset_version_id"], enforce_role=False)
        with self.assertRaises(DBAPIError):
            with self.engine.begin() as connection:
                connection.execute(asset_versions.update().where(asset_versions.c.asset_version_id == one["asset_version_id"])
                                   .values(storage_ref="replaced"))

    def test_sharing_top_level_never_grants_dependencies(self):
        a, b = self.scopes
        dependency = self.publish(a)
        parent = self.publish(a, dependencies=[dependency["asset_version_id"]])
        with self.assertRaises(PermissionError):
            grant_version(self.engine, a, parent["asset_version_id"], grantee_workspace_id=b.workspace_id)
        child_grant = grant_version(self.engine, a, dependency["asset_version_id"], grantee_workspace_id=b.workspace_id)
        grant_version(self.engine, a, parent["asset_version_id"], grantee_workspace_id=b.workspace_id)
        read_version(self.engine, b, parent["asset_version_id"], enforce_role=False)
        revoke_grant(self.engine, a, child_grant["grant_id"])
        with self.assertRaises(PermissionError):
            read_version(self.engine, b, parent["asset_version_id"], enforce_role=False)
        listing = list_versions(self.engine, b, enforce_role=False)
        self.assertFalse(next(row for row in listing["items"] if row["asset_version_id"] == parent["asset_version_id"])["available"])

    def test_owner_library_is_available_to_future_same_owner_sessions_only(self):
        a, b = self.scopes
        version = self.publish(a, provenance={"run_id": "run-a", "operation_id": "op-a"})
        grant_version(self.engine, a, version["asset_version_id"], grantee_owner_id=a.owner_id)
        read_version(self.engine, b, version["asset_version_id"], enforce_role=False)
        root = self.root / "other"
        root.mkdir()
        other = register_session_workspace(self.engine, host_id="host", session_id="session-other", owner_id="other", root_path=root)
        with self.assertRaises(PermissionError):
            read_version(self.engine, other, version["asset_version_id"], enforce_role=False)
        with self.engine.connect() as connection:
            provenance = connection.execute(select(asset_versions.c.provenance).where(
                asset_versions.c.asset_version_id == version["asset_version_id"])).scalar_one()
        self.assertEqual(provenance["session_id"], a.session_id)
        self.assertEqual(provenance["run_id"], "run-a")


if __name__ == "__main__":
    unittest.main()
