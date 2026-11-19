"""Task argument paths are authority-checked before trusted tools consume them."""

from pathlib import Path
from service_access.access_scope import current_access, workspace_path


_FILES = {"plan", "run", "input", "output", "dataset", "evidence", "items", "public_items", "bundle",
          "predictions", "script", "directory", "workspace", "source_image", "source_mask",
          "source_mesh", "source_gaussian", "media", "assets", "checkpoint"}
_CONFIG = {"config", "config_path", "service_config", "database_url", "operator_token", "python_command"}
# 远端资产内部的文件名由资产合同校验，不属于本机会话文件路径。
_RESOURCE_PATHS = {"relative_path"}


def _path_field(key, value):
    """识别需要绑定会话根目录的本地文件参数，保留远端资源名。"""
    return (isinstance(value, str) and bool(value)
            and key not in _RESOURCE_PATHS
            and (key in _FILES or key.endswith(("_path", "_dir", "_file")))
            and not value.startswith(("http://", "https://", "data:", "$")))


def resolve_arguments(arguments, workspace):
    """将本地任务路径绑定到会话目录，不改写资产内部路径或领域请求。"""
    check_arguments(arguments)
    root = Path(workspace).resolve()
    return {key: str((root / value).resolve()) if _path_field(key, value) else value
            for key, value in arguments.items()}


def check_arguments(arguments):
    """检查公开工具输入的文件权限，禁止调用方注入宿主身份与服务配置。"""
    if current_access() is None:
        return

    def check(value, key="", depth=0):
        """遍历输入结构；顶层本地路径受限，领域请求交给对应合同校验。"""
        if isinstance(value, dict):
            for name, child in value.items():
                if depth == 0 and name.startswith("_"):
                    raise PermissionError("Internal tool context cannot be supplied")
                if depth == 0 and name in _CONFIG and child:
                    raise PermissionError("Service configuration is selected by the Host")
                if depth == 0 and name in ('request', 'assessment', 'metadata'):
                    continue  # Native/domain contracts authorize their own typed resources.
                check(child, name, depth + 1)
        elif isinstance(value, list):
            for child in value:
                check(child, key, depth)
        elif isinstance(value, str) and value:
            # URLs, feature IDs and descriptive text are not local paths.
            if depth == 1 and _path_field(key, value):
                workspace_path(value, must_exist=False)
    check(arguments)
