"""Shared PostgreSQL persistence boundary for BenchForge services."""

from database.config import database_context, normalize_database_url, resolve_database_url, resolve_workspace_url


# 按需加载数据库入口，使轻量客户端校验配置时无需预先安装 SQLAlchemy。
def __getattr__(name):
    # Keep configuration validation usable in lightweight clients that do not
    # install the production SQLAlchemy extra.
    if name in {"engine_for", "workspace_id"}:
        from database.engine import engine_for, workspace_id
        return {"engine_for": engine_for,
                "workspace_id": workspace_id}[name]
    if name == "initialize_schema":
        from database.migrations import initialize_schema
        return initialize_schema
    raise AttributeError(name)

__all__ = [
    "database_context",
    "engine_for",
    "initialize_schema",
    "normalize_database_url",
    "resolve_database_url",
    "resolve_workspace_url",
    "workspace_id",
]
