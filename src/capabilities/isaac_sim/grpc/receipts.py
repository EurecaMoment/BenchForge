"""Cross-process ownership of one native request or artifact receipt."""
from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def receipt_lock(directory):
    """Serialize one receipt across subprocesses, not independent scenes."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.lock').open('a+b', buffering=0) as marker:
        if os.name == 'nt':
            import msvcrt
            if marker.seek(0, os.SEEK_END) == 0:
                marker.write(b'\0')
            marker.seek(0)
            msvcrt.locking(marker.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                marker.seek(0)
                msvcrt.locking(marker.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(marker, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(marker, fcntl.LOCK_UN)
