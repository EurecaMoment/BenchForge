"""Private operation output staging with descriptor-based workspace publication.

Only the trusted dispatcher creates a staging context. Public paths remain the
stable workspace paths used by receipts and database identities; capability code
writes into a private directory until the dispatcher publishes its projection.
"""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import hashlib
import os
from pathlib import Path
import stat
from tempfile import TemporaryDirectory

from storage.confined_files import copy_output, open_directory, open_read


_current = ContextVar('benchforge_operation_outputs', default=None)
_persistent = ContextVar('benchforge_persistent_outputs', default=())
_TEXT_SUFFIXES = {'.json', '.jsonl', '.md', '.txt', '.log', '.csv', '.yaml', '.yml'}


def _replace_prefix(value, source, destination):
    # A private stage is not a user-controlled file reference. Only translate
    # its exact directory prefix, including occurrences in diagnostic prose.
    return value.replace(source + '/', destination + '/').replace(source + '\\', destination + '\\') \
        if value != source else destination


def logical_path(value):
    path = Path(value)
    for output in _outputs():
        try:
            return output.destination / path.relative_to(output.directory)
        except ValueError:
            continue
    return path


def physical_path(value):
    path = Path(value)
    for output in _outputs():
        try:
            return output.directory / path.relative_to(output.destination)
        except ValueError:
            continue
    return path


def _outputs():
    current = _current.get()
    return (*reversed(_persistent.get()), *((current,) if current else ()))


def logical_value(value):
    """Translate only known staging prefixes; never infer or open resource paths."""
    if isinstance(value, dict):
        return {key: logical_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [logical_value(child) for child in value]
    if isinstance(value, tuple):
        return tuple(logical_value(child) for child in value)
    if isinstance(value, str):
        for output in _outputs():
            value = _replace_prefix(value, str(output.directory), str(output.destination))
    return value


def physical_value(value):
    if isinstance(value, dict):
        return {key: physical_value(child) for key, child in value.items()}
    if isinstance(value, list):
        return [physical_value(child) for child in value]
    if isinstance(value, str):
        for output in _outputs():
            value = _replace_prefix(value, str(output.destination), str(output.directory))
    return value


def operation_workspace(directory):
    output = _current.get()
    return output.workspace if output else Path(directory).parents[1]


def trusted_output_path(path):
    """Allow internal consumers to read this operation's own staged products."""
    candidate = Path(path)
    return any(candidate.is_absolute() and '..' not in candidate.parts
               and candidate.is_relative_to(output.directory)
               and candidate.resolve().is_relative_to(output.directory) for output in _outputs())


class OperationOutputs:
    def __init__(self, workspace, destination, directory, descriptor):
        self.workspace = Path(workspace)
        self.destination = Path(destination)
        self.directory = Path(directory)
        self.descriptor = descriptor
        self.published = {}

    def publish(self):
        """Publish regular files atomically while holding the destination root."""
        for current, folders, files in os.walk(self.directory, followlinks=False):
            for name in folders:
                if (Path(current) / name).is_symlink():
                    raise PermissionError('Operation outputs cannot contain linked directories')
            for name in files:
                if name == '.execution.lock':
                    continue
                path = Path(current) / name
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise PermissionError('Operation outputs must be regular files')
                relative = path.relative_to(self.directory)
                info = path.stat()
                version = (info.st_size, info.st_mtime_ns, info.st_ino)
                if self.published.get(relative) == version:
                    continue
                with open_read(self.directory, relative) as source:
                    if path.suffix.lower() in _TEXT_SUFFIXES and path.stat().st_size <= 64 * 1024 * 1024:
                        from io import BytesIO
                        raw = source.read()
                        for output in _outputs():
                            raw = raw.replace(str(output.directory).encode(), str(output.destination).encode())
                            # Preserve exact JSON formatting and number encodings.
                            raw = raw.replace(json.dumps(str(output.directory))[1:-1].encode(),
                                              json.dumps(str(output.destination))[1:-1].encode())
                        copy_output(self.descriptor, BytesIO(raw), relative, limit=len(raw))
                    else:
                        copy_output(self.descriptor, source, relative, limit=info.st_size)
                self.published[relative] = version


def publish_current():
    """Make a plan/review artifact available before committing its logical path."""
    for output in _outputs():
        output.publish()


@contextmanager
def persistent_outputs(destination, config=None, *, kind='pipeline-runs'):
    """Keep resumable run bytes private; PostgreSQL remains state authority."""
    from service_access.access_scope import current_access, workspace_path
    access = current_access()
    if access is None:
        yield Path(destination)
        return
    destination = logical_path(destination)
    destination = workspace_path(destination, must_exist=False)
    # Nested recipes belong to the already locked private run tree. Giving
    # them an unrelated physical root would break parent stage-receipt reads
    # and resumed output bindings after this nested context exits.
    for active in reversed(_persistent.get()):
        if destination.is_relative_to(active.destination):
            directory = active.directory / destination.relative_to(active.destination)
            from storage.execution_lock import exclusive_run
            with exclusive_run(directory):
                yield directory
            return
    configured = (config or {}).get('artifacts') or os.environ.get('BENCHFORGE_ARTIFACTS')
    if not configured:
        raise ValueError('Scoped pipeline execution requires the service artifacts directory')
    authority = Path(configured).resolve() / '.benchforge-private/workspaces' / access.scope.workspace_id
    if authority.is_relative_to(Path(access.root_path).resolve()):
        raise PermissionError('Pipeline authority must be outside the writable conversation')
    identity = hashlib.sha256(str(destination).encode()).hexdigest()
    if kind not in ('pipeline-runs', 'native-requests'):
        raise ValueError('Unknown persistent output kind')
    directory = authority / kind / identity
    if kind == 'native-requests':
        parent_identity = hashlib.sha256(str(destination.parent).encode()).hexdigest()
        directory = authority / kind / parent_identity / destination.name
    directory.mkdir(parents=True, exist_ok=True)
    from storage.execution_lock import exclusive_run
    with exclusive_run(directory), open_directory(access.root_path, destination, create=True) as descriptor:
        output = OperationOutputs(access.root_path, destination, directory, descriptor)
        token = _persistent.set((*_persistent.get(), output))
        try:
            yield directory
        finally:
            try:
                output.publish()
            finally:
                _persistent.reset(token)


@contextmanager
def operation_outputs(workspace, operation, *, scoped):
    destination = Path(workspace) / 'operations' / operation
    if not scoped:
        destination.mkdir(parents=True)
        yield destination
        return
    # The trusted root is held before execution. Replacing operations/<id>
    # cannot redirect publication into a directory outside this conversation.
    with (open_directory(workspace, Path('operations') / operation, create=True) as descriptor,
          TemporaryDirectory(prefix='benchforge-operation-') as temporary):
        if Path(temporary).resolve().is_relative_to(Path(workspace).resolve()):
            raise RuntimeError('Operation staging must stay outside the task workspace')
        directory = Path(temporary) / 'operations' / operation
        directory.mkdir(parents=True)
        output = OperationOutputs(workspace, destination, directory, descriptor)
        token = _current.set(output)
        try:
            yield directory
        finally:
            try:
                output.publish()
            finally:
                _current.reset(token)
