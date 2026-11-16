"""Cross-process ownership of one native request or artifact receipt."""
from pathlib import Path
from storage.file_lock import file_lock


def receipt_lock(directory):
    """Serialize one receipt across subprocesses, not independent scenes."""
    return file_lock(Path(directory) / '.lock')
