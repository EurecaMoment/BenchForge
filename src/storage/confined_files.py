"""Descriptor-relative file access on Linux 5.4, without following task symlinks."""

from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import stat
import uuid


def _parts(root, file, *, allow_root=False):
    candidate = Path(file)
    if isinstance(root, int):
        if candidate.is_absolute():
            raise PermissionError('Descriptor-relative paths must be relative')
        relative = candidate
    else:
        root = Path(root).absolute()
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            relative = candidate.relative_to(root)
        except ValueError as exc:
            raise PermissionError('File is outside the authorized root') from exc
    if (not relative.parts and not allow_root) or any(part in ('.', '..') for part in relative.parts):
        raise PermissionError('Invalid relative file path')
    return root, relative.parts


@contextmanager
def open_directory(root, directory='.', *, create=False):
    """Pin a directory below a trusted root; optionally create missing parents."""
    root, parts = _parts(root, directory, allow_root=True)
    if os.name != 'posix':
        raise RuntimeError('Confined service file access requires Linux descriptor access')
    descriptor = os.dup(root) if isinstance(root, int) else os.open(
        root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise PermissionError('Authorized root must be a directory')
        for part in parts:
            if create:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass  # Opening below rejects files and symbolic links.
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def mkdir(root, directory):
    """Create task directories without following a mutable parent symlink."""
    with open_directory(root, directory, create=True):
        pass


@contextmanager
def open_read(root, file):
    """Hold every parent by descriptor so rename/symlink races cannot escape."""
    root, parts = _parts(root, file)
    with open_directory(root, Path(*parts[:-1])) as descriptor:
        file_descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            if not stat.S_ISREG(os.fstat(file_descriptor).st_mode):
                raise PermissionError('Only regular input files may be consumed')
            with os.fdopen(file_descriptor, 'rb') as stream:
                file_descriptor = None
                yield stream
        finally:
            if file_descriptor is not None:
                os.close(file_descriptor)


@contextmanager
def open_write(root, file):
    """Atomically replace a task file via its pinned parent, never its target inode.

    Existing symlink or hardlink targets are replaced as directory entries; no
    write reaches their referent. The destination is published only on success.
    """
    root, parts = _parts(root, file)
    with open_directory(root, Path(*parts[:-1]), create=True) as descriptor:
        temporary = '.output-' + uuid.uuid4().hex
        file_descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                  0o600, dir_fd=descriptor)
        try:
            with os.fdopen(file_descriptor, 'wb') as stream:
                file_descriptor = None
                yield stream
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, parts[-1], src_dir_fd=descriptor, dst_dir_fd=descriptor)
        finally:
            if file_descriptor is not None:
                os.close(file_descriptor)
            try:
                os.unlink(temporary, dir_fd=descriptor)
            except FileNotFoundError:
                pass


def read_bytes(root, file, *, limit):
    with open_read(root, file) as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError('Input file exceeds the supported size')
    return value


def copy_input(root, source, destination, *, limit=512 * 1024 * 1024):
    """Destination must be a private service staging path, never model-selected."""
    destination = Path(destination)
    with open_read(root, source) as stream:
        size = os.fstat(stream.fileno()).st_size
        if size > limit:
            raise ValueError('Input file exceeds the supported size')
        with destination.open('xb') as output:
            shutil.copyfileobj(stream, output, 1024 * 1024)
    return destination


def copy_output(root, source, destination, *, limit=512 * 1024 * 1024):
    """Publish a trusted binary stream into a mutable task tree atomically.

    The caller owns the source stream and its position. No task-selected source
    path is opened here; use open_read for task inputs.
    """
    with open_write(root, destination) as output:
        remaining = limit
        while True:
            block = source.read(min(1024 * 1024, remaining + 1))
            if not block:
                break
            remaining -= len(block)
            if remaining < 0:
                raise ValueError('Output file exceeds the supported size')
            output.write(block)
    return destination
