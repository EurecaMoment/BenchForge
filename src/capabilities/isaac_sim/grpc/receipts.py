"""Cross-process ownership of one native request or artifact receipt."""
from pathlib import Path
from storage.file_lock import file_lock


# 跨进程串行化同一目录的回执读写，不阻塞独立场景的执行。
def receipt_lock(directory):
    """Serialize one receipt across subprocesses, not independent scenes."""
    return file_lock(Path(directory) / '.lock')
