"""Immutable shared asset versions and explicit grants, never directory grants.

The trusted asset service verifies content hashes and reference closure before
publishing. This repository stores verified manifests; it never opens arbitrary
paths or interprets a model-authored manifest as proof of file ownership.
"""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePosixPath
import re
import uuid

from sqlalchemy import and_, func, or_, select
from database.access import identifier, read_transaction, validate_scope
from database.schema import assets, asset_versions, asset_grants, workspaces
from database.transactions import lock_entity, insert_statement


def validate_manifest(manifest):
    if not isinstance(manifest, list) or not 1 <= len(manifest) <= 4096:
        raise ValueError("Asset manifest must contain 1..4096 files")
    files, names = [], set()
    for item in manifest:
        if not isinstance(item, dict) or set(item) != {"path", "sha256", "bytes"}:
            raise ValueError("Manifest entries require path, sha256 and bytes")
        name = item["path"]
        if (not isinstance(name, str) or not name or len(name) > 1000 or "\\" in name or ":" in name
                or any(ord(char) < 32 for char in name) or PurePosixPath(name).is_absolute()
                or any(part in ("", ".", "..") for part in name.split("/"))):
            raise ValueError("Manifest paths must be relative canonical file names")
        if name in names:
            raise ValueError("Duplicate asset file")
        if not isinstance(item["sha256"], str) or not re.fullmatch("[a-f0-9]{64}", item["sha256"]):
            raise ValueError("Invalid asset content digest")
        if type(item["bytes"]) is not int or item["bytes"] < 0:
            raise ValueError("Invalid asset file size")
        names.add(name)
        files.append(dict(item))
    return sorted(files, key=lambda row: row["path"])


def _metadata(value):
    allowed = {"description", "license", "source", "units", "coordinate_system", "classification", "name", "kind"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("Unsupported public asset metadata")
    if any(not isinstance(item, str) or len(item) > 2000 for item in value.values()):
        raise ValueError("Asset metadata values must be bounded strings")
    if value.get("classification", "public_input") not in {"public_input", "production_observation"}:
        raise ValueError("Private evaluation authority cannot be a shared asset")
    return {**value, "classification": value.get("classification", "public_input")}


def _grant_filter(scope, *, derive=False):
    conditions = [asset_grants.c.revoked_at.is_(None),
                  or_(asset_grants.c.expires_at.is_(None), asset_grants.c.expires_at > func.now()),
                  or_(asset_grants.c.audience == "public", asset_grants.c.grantee_workspace_id == scope.workspace_id,
                      asset_grants.c.grantee_owner_id == scope.owner_id)]
    if derive:
        conditions.append(asset_grants.c.allow_derive.is_(True))
    return and_(*conditions)


def _visible(scope, *, derive=False):
    return or_(asset_versions.c.workspace_id == scope.workspace_id,
               asset_versions.c.asset_version_id.in_(select(asset_grants.c.asset_version_id).where(_grant_filter(scope, derive=derive))))


def _version(connection, scope, version_id, *, derive=False):
    identifier(version_id, "asset version")
    row = connection.execute(select(asset_versions).where(
        asset_versions.c.asset_version_id == version_id, _visible(scope, derive=derive))).mappings().first()
    if row is None:
        raise PermissionError("Asset version is unavailable")
    return row


def _closure(connection, scope, row, seen=None):
    seen = set() if seen is None else seen
    if row["asset_version_id"] in seen:
        return
    if len(seen) >= 256:
        raise ValueError("Asset dependency closure exceeds 256 versions")
    seen.add(row["asset_version_id"])
    for dependency in row["dependencies"]:
        _closure(connection, scope, _version(connection, scope, dependency), seen)


def publish_version(engine, scope, *, name, kind, manifest, storage_ref, metadata=None,
                    asset_id=None, derived_from_version_id=None, dependencies=(), provenance=None):
    """Trusted service only; storage_ref identifies already verified private storage."""
    files = validate_manifest(manifest)
    if any(not isinstance(value, str) or not 1 <= len(value) <= 200 for value in (name, kind)):
        raise ValueError("Asset name and kind must contain 1..200 characters")
    if (not isinstance(storage_ref, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,999}", storage_ref)
            or any(part in ("", ".", "..") for part in storage_ref.split("/"))):
        raise ValueError("storage_ref must be an opaque relative service storage reference")
    if not isinstance(dependencies, (list, tuple)) or len(dependencies) > 128:
        raise ValueError("Invalid asset dependencies")
    dependencies = sorted(set(identifier(item, "dependency version") for item in dependencies))
    public = _metadata({**(metadata or {}), "name": name, "kind": kind})
    provenance = dict(provenance or {})
    if set(provenance) - {"run_id", "operation_id", "tool", "source_asset_id"}:
        raise ValueError("Unsupported asset provenance fields")
    for key, value in provenance.items():
        identifier(value, key)
    provenance.update(host_id=scope.host_id, session_id=scope.session_id, workspace_id=scope.workspace_id)
    digest = hashlib.sha256(json.dumps({"files": files, "dependencies": dependencies}, sort_keys=True,
                                      separators=(",", ":")).encode()).hexdigest()
    aid = identifier(asset_id or "asset-" + uuid.uuid4().hex, "asset")
    with engine.begin() as connection:
        validate_scope(connection, scope)
        lock_entity(connection, "asset-version", aid)
        old = connection.execute(select(assets).where(assets.c.asset_id == aid)).mappings().first()
        if old and old["workspace_id"] != scope.workspace_id:
            raise PermissionError("Asset is unavailable")
        if derived_from_version_id:
            _version(connection, scope, derived_from_version_id, derive=True)
        for dependency in dependencies:
            _closure(connection, scope, _version(connection, scope, dependency))
        if not old:
            connection.execute(assets.insert().values(asset_id=aid, workspace_id=scope.workspace_id,
                session_id=scope.session_id, name=name, kind=kind))
        number = (connection.execute(select(func.max(asset_versions.c.version)).where(asset_versions.c.asset_id == aid)).scalar() or 0) + 1
        vid = "version-" + uuid.uuid4().hex
        connection.execute(asset_versions.insert().values(asset_version_id=vid, asset_id=aid,
            workspace_id=scope.workspace_id, version=number, storage_ref=storage_ref, content_hash=digest,
            manifest=files, dependencies=dependencies, metadata=public, provenance=provenance,
            derived_from_version_id=derived_from_version_id))
        return {"asset_id": aid, "asset_version_id": vid, "version": number, "content_hash": digest}


def grant_version(engine, scope, version_id, *, grantee_workspace_id=None, grantee_owner_id=None, public=False,
                  allow_derive=False, expires_at=None):
    if type(public) is not bool or type(allow_derive) is not bool or sum((public, bool(grantee_workspace_id), bool(grantee_owner_id))) != 1:
        raise ValueError("Choose exactly one public, owner or workspace audience")
    if grantee_workspace_id:
        identifier(grantee_workspace_id, "grantee workspace")
    if grantee_owner_id:
        identifier(grantee_owner_id, "grantee owner")
        if grantee_owner_id != scope.owner_id:
            raise PermissionError("Personal library grants must name the authenticated owner")
    if expires_at is not None and (not isinstance(expires_at, datetime) or expires_at.tzinfo is None
                                   or expires_at <= datetime.now(timezone.utc)):
        raise ValueError("Grant expiry must be a future timezone-aware time")
    with engine.begin() as connection:
        validate_scope(connection, scope)
        row = _version(connection, scope, version_id)
        if row["workspace_id"] != scope.workspace_id:
            raise PermissionError("Only the asset owner may grant access")
        if row["metadata"].get("classification") == "production_observation" and public:
            raise PermissionError("Production observations require an explicit workspace audience")
        if grantee_workspace_id and not connection.execute(select(workspaces.c.workspace_id).where(
                workspaces.c.workspace_id == grantee_workspace_id, workspaces.c.state == "active")).first():
            raise PermissionError("Grantee workspace is unavailable")
        audience = "public" if public else "owner:" + grantee_owner_id if grantee_owner_id else "workspace:" + grantee_workspace_id
        lock_entity(connection, "asset-grant", version_id, audience)
        # Sharing a top-level asset never implicitly shares its dependencies.
        for dependency in row["dependencies"]:
            query = select(asset_grants.c.grant_id).where(asset_grants.c.asset_version_id == dependency,
                asset_grants.c.revoked_at.is_(None), asset_grants.c.expires_at.is_(None),
                asset_grants.c.audience == "public" if public else asset_grants.c.audience.in_(["public", audience]))
            dependency_owner = connection.execute(select(asset_versions.c.workspace_id).where(
                asset_versions.c.asset_version_id == dependency)).scalar_one()
            if not (grantee_workspace_id and dependency_owner == grantee_workspace_id) and not connection.execute(query).first():
                raise PermissionError("Grant dependency versions explicitly before sharing this version")
        values = dict(grant_id="grant-" + uuid.uuid4().hex, asset_version_id=version_id,
            workspace_id=scope.workspace_id, audience=audience, grantee_workspace_id=grantee_workspace_id,
            grantee_owner_id=grantee_owner_id,
            allow_derive=allow_derive, expires_at=expires_at, revoked_at=None)
        update = {key: value for key, value in values.items() if key != "grant_id"}
        result = connection.execute(insert_statement(connection, asset_grants).values(**values)
            .on_conflict_do_update(index_elements=["asset_version_id", "audience"], set_=update)
            .returning(asset_grants.c.grant_id)).scalar_one()
        return {"grant_id": result, "asset_version_id": version_id, "audience": audience}


def revoke_grant(engine, scope, grant_id):
    identifier(grant_id, "grant")
    with engine.begin() as connection:
        validate_scope(connection, scope)
        result = connection.execute(asset_grants.update().where(asset_grants.c.grant_id == grant_id,
            asset_grants.c.workspace_id == scope.workspace_id).values(revoked_at=func.now()))
        if result.rowcount != 1:
            raise PermissionError("Asset grant is unavailable")


def _public(row):
    return {key: row[key] for key in ("asset_version_id", "asset_id", "version", "content_hash", "manifest", "dependencies", "metadata")}


def resolve_asset_version(engine, scope, asset_id, *, enforce_role=False):
    """Trusted resolver; grants are checked before returning private storage metadata."""
    identifier(asset_id, "asset")
    with read_transaction(engine, scope, enforce_role=enforce_role) as connection:
        row = connection.execute(select(asset_versions).where(asset_versions.c.asset_id == asset_id,
            _visible(scope)).order_by(asset_versions.c.version.desc()).limit(1)).mappings().first()
        if row is None:
            raise PermissionError("Asset is unavailable")
        _closure(connection, scope, row)
        return dict(row)


def resolve_storage_version(engine, scope, storage_ref):
    """Trusted resolver for a path already recognized as private asset storage."""
    with read_transaction(engine, scope, enforce_role=False) as connection:
        row = connection.execute(select(asset_versions).where(asset_versions.c.storage_ref == storage_ref,
            _visible(scope))).mappings().first()
        if row is None:
            raise PermissionError("Asset file is unavailable")
        _closure(connection, scope, row)
        return dict(row)


def read_version(engine, scope, version_id, *, enforce_role=True):
    with read_transaction(engine, scope, enforce_role=enforce_role) as connection:
        row = _version(connection, scope, version_id)
        _closure(connection, scope, row)
        return _public(row)


def list_versions(engine, scope, *, limit=30, after=None, enforce_role=True):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Asset query limit must be 1..100")
    clauses = [_visible(scope)]
    if after is not None:
        clauses.append(asset_versions.c.asset_version_id > identifier(after, "asset cursor"))
    with read_transaction(engine, scope, enforce_role=enforce_role) as connection:
        rows = connection.execute(select(asset_versions).where(*clauses).order_by(asset_versions.c.asset_version_id).limit(limit + 1)).mappings().all()
        items = []
        for row in rows[:limit]:
            value = _public(row)
            try:
                _closure(connection, scope, row)
                value["available"] = True
            except PermissionError:
                # Do not reveal inaccessible dependency identities.
                value.pop("dependencies", None)
                value["available"] = False
            items.append(value)
    return {"items": items, "next": rows[limit - 1]["asset_version_id"] if len(rows) > limit else None}
