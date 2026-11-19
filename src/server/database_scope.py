"""Bind Host helper commands to the same database as production tools."""

from contextlib import nullcontext
import os

from database.config import configured_database_url, database_context
from service_access.runtime_config import load_service_config


# 为命令行调用绑定配置指定的数据库；没有配置时保留当前上下文。
def command_database(config_path):
    config_path = config_path or os.environ.get("BENCHFORGE_SERVICE_CONFIG")
    if not config_path:
        return nullcontext()
    config = load_service_config(config_path, required_tokens=())
    url = configured_database_url(config)
    return database_context(url) if url else nullcontext()
