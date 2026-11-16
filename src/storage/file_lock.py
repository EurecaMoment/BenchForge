"""OS-owned locks on persistent marker files shared by local subprocesses."""

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import time


class LockBusyError(RuntimeError):
    """Another process owns the requested marker."""


@contextmanager
def file_lock(path, *, blocking=True):
    """Lock byte zero even on an empty file; never write before acquiring it.

    Windows permits locking beyond EOF. Keeping the marker (and its inode)
    after release also prevents two callers from locking different files.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b", buffering=0) as stream:
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
