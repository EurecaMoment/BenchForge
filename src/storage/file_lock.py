"""OS-owned locks on persistent marker files shared by local subprocesses."""

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import time


@contextmanager
def _marker(path):
    from service_access.access_scope import current_access
    access = current_access()
    candidate = path if path.is_absolute() else Path(access.root_path) / path if access else path
    if access and candidate.is_relative_to(Path(access.root_path)):
        from storage.confined_files import open_directory
        with open_directory(access.root_path, candidate.parent, create=True) as parent:
            descriptor = os.open(candidate.name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 0o600, dir_fd=parent)
            import stat
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                os.close(descriptor)
                raise PermissionError('Lock marker must be a regular file')
            with os.fdopen(descriptor, 'r+b', buffering=0) as stream:
                yield stream
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b', buffering=0) as stream:
        yield stream


class LockBusyError(RuntimeError):
    """Another process owns the requested marker."""


@contextmanager
def file_lock(path, *, blocking=True):
    """Lock byte zero even on an empty file; never write before acquiring it.

    Windows permits locking beyond EOF. Keeping the marker (and its inode)
    after release also prevents two callers from locking different files.
    """
    path = Path(path)
    with _marker(path) as stream:
        if os.name == "nt":
            import msvcrt

            while True:
                try:
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                        raise
                    if not blocking:
                        raise LockBusyError(str(path)) from error
                    time.sleep(0.05)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(stream, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError as error:
                raise LockBusyError(str(path)) from error
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
