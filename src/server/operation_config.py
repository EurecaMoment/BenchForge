"""Compose provider configuration and the shared service database at CLI entry."""

import os

from database.config import configured_database_url
from service_access.runtime_config import load_service_config, read_json


def load_operation_config(path=None):
    config = read_json(path) if path else {}
    if path and config.get("service_config"):
        service = load_service_config(path, required_tokens=())
        return {**config, "database_url": service["database_url"]}
    if configured_database_url(config) is None:
        service_path = os.environ.get("BENCHFORGE_SERVICE_CONFIG")
        if service_path:
            service = load_service_config(service_path, required_tokens=())
            config = {**config, "database_url": service["database_url"]}
    return config
