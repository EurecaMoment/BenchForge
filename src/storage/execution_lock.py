"""One process owns a pipeline run while it advances its persisted state."""
from contextlib import contextmanager
from pathlib import Path
from storage.file_lock import file_lock, LockBusyError


class RunBusyError(RuntimeError):
    """The same run is already being advanced by another caller."""


# 持有运行目录的非阻塞本地锁，已有执行者时返回明确的忙碌错误。
@contextmanager
def exclusive_run(directory):
    """Reject competing local execution; distributed stages use database leases."""
    root = Path(directory)
    try:
        with file_lock(root / '.execution.lock', blocking=False):
            yield
    except LockBusyError as error:
        raise RunBusyError(
            f"Pipeline run is already executing: {root}. "
            "Read its status or resume after the current executor exits."
        ) from error
