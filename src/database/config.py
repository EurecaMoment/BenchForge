"""PostgreSQL URL resolution and validation."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


_database_url = ContextVar("benchforge_database_url", default=None)


def normalize_database_url(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Configure BENCHFORGE_DATABASE_URL for PostgreSQL")
    try:
        parts = urlsplit(value)
    except ValueError:
        raise ValueError("Invalid PostgreSQL database URL") from None
    if parts.scheme in ("postgres", "postgresql"):
        return "postgresql+psycopg:" + value.split(":", 1)[1]
    if parts.scheme == "postgresql+psycopg":
        return value
    raise ValueError("BenchForge requires a PostgreSQL database URL")


def configured_database_url(config=None):
    """Resolve explicit config without changing the process environment."""
    config = config or {}
    variable = config.get("database_url_env")
    if variable:
        value = os.environ.get(variable)
        if not value:
            raise ValueError(f"database_url_env references unset environment variable {variable}")
        return normalize_database_url(value)
    if config.get("database_url") is not None:
        return normalize_database_url(config["database_url"])
    return None


def _configured_url(database_url=None):
    return next((value for value in (database_url, _database_url.get(),
                                    os.environ.get("BENCHFORGE_DATABASE_URL"))
                 if value is not None), None)


@contextmanager
def database_context(url):
    """Bind nested domain calls to this request's database, restoring on exit."""
    token = _database_url.set(normalize_database_url(url))
    try:
        yield
    finally:
        _database_url.reset(token)


def resolve_database_url(harness=None, database_url=None):
    value = _configured_url(database_url)
    if value is None and harness:
        receipt = Path(harness) / "deployment.local.json"
        if receipt.is_file():
            value = json.loads(receipt.read_text(encoding="utf-8")).get("database_url")
    return normalize_database_url(value)


def resolve_workspace_url(workspace, database_url=None):
    """Resolve the shared database; workspace isolation belongs to row identity."""
    return resolve_database_url(database_url=database_url)
