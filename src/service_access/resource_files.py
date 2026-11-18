"""Resolve task inputs and owned production evidence without sharing file roots."""

from contextlib import contextmanager
import os
from pathlib import Path

from sqlalchemy import select
from database.access import validate_scope
from database.engine import engine_for
from database.schema import runs, work_items
from service_access.access_scope import current_access, workspace_path
from service_access.runtime_config import environment_value
from storage.confined_files import open_read


def _resource(engine, artifact_root, file, *, must_exist):
    access = current_access()
    path = Path(file).expanduser()
    if access is None:
        return path.resolve(strict=must_exist), None
    workspace = Path(access.root_path).resolve()
    candidate = path if path.is_absolute() else workspace / path
    if candidate.is_relative_to(workspace):
        return workspace_path(candidate, must_exist=must_exist), workspace
    configured = artifact_root or environment_value('BENCHFORGE_ARTIFACTS')
    if not configured:
        raise PermissionError('Production evidence requires its configured artifact root')
    root = Path(configured).resolve()
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise PermissionError('File is outside this conversation and its production evidence') from exc
    if not relative.parts or '..' in relative.parts:
        raise PermissionError('Invalid production evidence path')
    with (engine or engine_for()).connect() as connection:
        validate_scope(connection, access.scope)
        owner = connection.execute(select(runs.c.workspace_id, runs.c.session_id).join(
            work_items, work_items.c.run_id == runs.c.id).where(
                work_items.c.id == relative.parts[0])).mappings().first()
    if (not owner or owner['workspace_id'] not in access.scope.ledger_namespaces
            or owner['session_id'] != access.scope.session_id):
        raise PermissionError('Production evidence belongs to another conversation')
    for parent in (candidate, *candidate.parents):
        if parent == root:
            break
        if parent.is_symlink():
            raise PermissionError('Production evidence cannot follow symbolic links')
    resolved = candidate.resolve(strict=must_exist)
    if not resolved.is_relative_to(root):
        raise PermissionError('Production evidence path escaped its configured root')
    return resolved, root


def resolve_owned_resource(engine, artifact_root, file, *, must_exist=True):
    """Authorize a location; consumers must use open_owned_resource for bytes."""
    return _resource(engine, artifact_root, file, must_exist=must_exist)[0]


@contextmanager
def open_owned_resource(file, *, engine=None, artifact_root=None):
    path, root = _resource(engine, artifact_root, file, must_exist=True)
    if root is None:
        with path.open('rb') as stream:
            yield stream
    else:
        with open_read(root, path) as stream:
            yield stream


def read_owned_resource(file, *, limit, engine=None, artifact_root=None):
    """Return a bounded snapshot and the version of that same file descriptor."""
    with open_owned_resource(file, engine=engine, artifact_root=artifact_root) as stream:
        metadata = os.fstat(stream.fileno())
        content = stream.read(limit + 1)
        if len(content) > limit:
            raise ValueError('Input file exceeds its supported size')
    return content, [metadata.st_size, metadata.st_mtime_ns]


def owned_resource_version(file, *, engine=None, artifact_root=None):
    with open_owned_resource(file, engine=engine, artifact_root=artifact_root) as stream:
        metadata = os.fstat(stream.fileno())
    return [metadata.st_size, metadata.st_mtime_ns]
