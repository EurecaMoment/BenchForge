"""Task labels resolve only within the verified conversation workspace."""

from pathlib import Path
import re
from service_access.access_scope import current_access, workspace_path
from storage.confined_files import mkdir


def task_directory(workspace_id):
    if not isinstance(workspace_id, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", workspace_id):
        raise ValueError("invalid workspace_id task label")
    workspace = workspace_path(Path('tasks') / workspace_id / 'work', must_exist=False)
    mkdir(current_access().root_path, workspace)
    return workspace_path(workspace)
