"""Verified production bundles in a private, owner-shared asset library."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import shlex
import shutil
import struct
from urllib.parse import unquote, urlsplit
import uuid

from database.engine import engine_for
from database.access import validate_scope
from service_access.task_access import task_access as task_access
from storage.shared_assets import (publish_version, grant_version, list_versions,
                                   resolve_asset_version, resolve_storage_version)


def _file(root, relative):
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("Asset references must be relative files")
    parts = relative.split("/")
    if PurePosixPath(relative).is_absolute() or any(part in ("", ".", "..") for part in parts):
        raise ValueError("Asset reference escapes its bundle")
    candidate = root.joinpath(*parts)
    for item in [candidate, *candidate.parents]:
        if item == root:
            break
        if item.is_symlink():
            raise ValueError("Asset links are not publishable")
    if not candidate.resolve(strict=True).is_relative_to(root.resolve(strict=True)) or not candidate.is_file():
        raise ValueError("Asset dependency is missing")
    return candidate


def _gltf_document(path):
    if path.suffix.lower() == ".gltf":
        return json.loads(path.read_text(encoding="utf-8"))
    with path.open("rb") as source:
        header = source.read(20)
        if len(header) != 20:
            raise ValueError("Truncated GLB")
        magic, version, length, chunk_length, chunk_type = struct.unpack("<5I", header)
        if magic != 0x46546C67 or version != 2 or length != path.stat().st_size or chunk_type != 0x4E4F534A or chunk_length > min(length - 20, 32 * 1024 * 1024):
            raise ValueError("Invalid GLB header")
        return json.loads(source.read(chunk_length).decode("utf-8"))


def _references(file):
    refs = []
    if file.suffix.lower() in {".glb", ".gltf"}:
        def visit(value):
            if isinstance(value, dict):
                for key, child in value.items():
                    if key == "uri":
                        refs.append(child)
                    else:
                        visit(child)
            elif isinstance(value, list):
                for child in value:
                    visit(child)
        visit(_gltf_document(file))
    elif file.suffix.lower() in {".obj", ".mtl"}:
        for line in file.read_text(encoding="utf-8").splitlines():
            fields = shlex.split(line, comments=True)
            if fields and (fields[0] == "mtllib" or fields[0].lower().startswith("map_") or fields[0] in {"bump", "disp", "decal"}):
                if len(fields) != 2:
                    raise ValueError("Material references must use one explicit relative file")
                refs.append(fields[1])
    return refs


def _relative_reference(relative, reference):
    if not isinstance(reference, str):
        raise ValueError("Invalid asset URI")
    if reference.startswith("data:"):
        return None
    if urlsplit(reference).scheme or reference.startswith(("/", "\\")):
        raise ValueError("External asset URI is not authorized")
    decoded = unquote(reference)
    if "\\" in decoded or ":" in decoded or any(part in ("", ".", "..") for part in decoded.split("/")):
        raise ValueError("Asset dependency escapes its bundle")
    return (PurePosixPath(relative).parent / decoded).as_posix()


def verify_reference_closure(source):
    """Check external dependencies before a general mesh loader can follow them."""
    source = Path(source)
    root = source.parent.resolve(strict=True)
    pending, seen = [source.name], set()
    while pending:
        relative = pending.pop()
        if relative in seen:
            continue
        if len(seen) >= 4096:
            raise ValueError("Asset dependency closure is too large")
        file = _file(root, relative)
        seen.add(relative)
        for reference in _references(file):
            joined = _relative_reference(relative, reference)
            if joined is None:
                continue
            _file(root, joined)
            pending.append(joined)
    return seen


def _digest(file):
    digest = hashlib.sha256()
    with file.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_input(artifact_root, access, source):
    """Copy by workspace dirfd first; parse references only in private staging."""
    from storage.confined_files import copy_input
    source = Path(source)
    base = Path(artifact_root).resolve() / ".generation-inputs" / access.scope.workspace_id
    if base.is_relative_to(Path(access.root_path).resolve()):
        raise PermissionError("Generation staging must be outside the model workspace")
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = base / ("pending-" + uuid.uuid4().hex)
    staging.mkdir(mode=0o700)
    pending, copied = [source.name], {}
    while pending:
        relative = pending.pop()
        if relative in copied:
            continue
        if len(copied) >= 4096:
            raise ValueError("Asset dependency closure is too large")
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        copy_input(access.root_path, source.parent / relative, target)
        copied[relative] = _digest(target)
        for reference in _references(target):
            dependency = _relative_reference(relative, reference)
            if dependency is not None:
                pending.append(dependency)
    digest = hashlib.sha256(json.dumps(copied, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    destination = base / digest
    try:
        staging.rename(destination)
    except OSError:
        if not destination.is_dir() or any(_digest(_file(destination, name)) != value for name, value in copied.items()):
            raise
        # The unpublished duplicate stays private for explicit later cleanup.
        # Avoid deleting any path that may still be held by a concurrent reader.
    for name in copied:
        (destination / name).chmod(0o400)
    return destination / source.name


def verify_bundle(root):
    root = Path(root).resolve(strict=True)
    mesh = json.loads(_file(root, "mesh.json").read_text(encoding="utf-8"))
    verify_reference_closure(_file(root, "asset.glb"))
    for material in mesh.get("materials", []):
        for reference in material.get("textures", {}).values():
            _file(root, reference)
    files = []
    for file in sorted(root.rglob("*")):
        if file.is_symlink():
            raise ValueError("Asset bundles cannot contain links")
        if file.is_file():
            relative = file.relative_to(root).as_posix()
            _file(root, relative)
            files.append({"path": relative, "bytes": file.stat().st_size, "sha256": _digest(file)})
    if len(files) > 4096:
        raise ValueError("Asset bundle exceeds 4096 files")
    return files


def publish_generated(artifact_root, access, source, record, *, engine=None, run_id=None, operation_id=None):
    """Snapshot verified service output, then publish to this owner's private library."""
    engine = engine or engine_for()
    with engine.connect() as connection:
        validate_scope(connection, access.scope)
    source = Path(source).resolve(strict=True)
    initial = verify_bundle(source)
    library = Path(artifact_root).resolve() / ".shared-assets"
    if library.is_relative_to(Path(access.root_path).resolve()):
        raise PermissionError("Shared asset storage must be outside the model workspace")
    library.mkdir(parents=True, exist_ok=True, mode=0o700)
    storage_ref = "bundles/" + uuid.uuid4().hex
    destination = library / storage_ref
    destination.mkdir(parents=True, mode=0o700)
    for file in initial:
        target = destination / file["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_file(source, file["path"]), target)
        if target.stat().st_size != file["bytes"] or _digest(target) != file["sha256"]:
            raise ValueError("Asset changed while publishing; no version was registered")
    # Shared metadata never transfers source paths, prompts, or raw session records.
    public = {key: record[key] for key in ("asset_id", "label", "status", "size_bounds", "geometry", "physics", "appearance", "gaussian") if key in record}
    (destination / "asset.json").write_text(json.dumps(public, ensure_ascii=False), encoding="utf-8")
    manifest = verify_bundle(destination)
    provenance = {"source_asset_id": record["asset_id"], "tool": "asset_generation"}
    if run_id:
        provenance["run_id"] = run_id
    if operation_id:
        provenance["operation_id"] = operation_id
    result = publish_version(engine, access.scope, name=record["label"], kind="mesh", manifest=manifest,
        storage_ref=storage_ref, asset_id=record["asset_id"], provenance=provenance,
        metadata={"units": "meters", "coordinate_system": record.get("geometry", {}).get("coordinate_frame", "unspecified")})
    grant_version(engine, access.scope, result["asset_version_id"], grantee_owner_id=access.scope.owner_id, allow_derive=True)
    for file in destination.rglob("*"):
        if file.is_file():
            file.chmod(0o400)
    return result


def asset_file(artifact_root, access, asset_id, filename="asset.json", *, engine=None):
    row = resolve_asset_version(engine or engine_for(), access.scope, asset_id)
    file = next((item for item in row["manifest"] if item["path"] == filename), None)
    if file is None:
        raise PermissionError("Asset file is unavailable")
    library = Path(artifact_root).resolve() / ".shared-assets"
    root = library / row["storage_ref"]
    if not root.resolve(strict=True).is_relative_to(library.resolve(strict=True)):
        raise PermissionError("Invalid asset storage reference")
    path = _file(root, filename)
    if path.stat().st_size != file["bytes"] or _digest(path) != file["sha256"]:
        raise ValueError("Published asset integrity check failed")
    return path


def authorized_source(artifact_root, access, source, *, engine=None):
    library = Path(artifact_root).resolve() / ".shared-assets"
    path = Path(source).resolve(strict=True)
    relative = path.relative_to(library.resolve(strict=True))
    if len(relative.parts) < 3 or relative.parts[0] != "bundles":
        raise PermissionError("Asset file is unavailable")
    row = resolve_storage_version(engine or engine_for(), access.scope, "/".join(relative.parts[:2]))
    filename = "/".join(relative.parts[2:])
    item = next((item for item in row["manifest"] if item["path"] == filename), None)
    if item is None or path.stat().st_size != item["bytes"] or _digest(path) != item["sha256"]:
        raise PermissionError("Asset file is unavailable or changed")
    return _file(library / row["storage_ref"], filename)


def catalog(artifact_root, access, asset_ids=None, *, engine=None):
    engine = engine or engine_for()
    records, after, seen = [], None, set()
    while True:
        page = list_versions(engine, access.scope, limit=100, after=after, enforce_role=False)
        for version in page["items"]:
            if not version["available"] or version["asset_id"] in seen or asset_ids is not None and version["asset_id"] not in asset_ids:
                continue
            seen.add(version["asset_id"])
            latest = resolve_asset_version(engine, access.scope, version["asset_id"])
            record = json.loads(asset_file(artifact_root, access, version["asset_id"], engine=engine).read_text(encoding="utf-8"))
            records.append({**record, "asset_version_id": latest["asset_version_id"], "version": latest["version"]})
        after = page["next"]
        if not after:
            return records
