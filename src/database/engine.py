"""Process-owned, bounded connection pools shared by domain repositories."""

import atexit
from collections import OrderedDict
import hashlib
import os
from pathlib import Path
from threading import RLock

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url

from database.config import resolve_database_url


_engines = OrderedDict()
_lock = RLock()
_pid = os.getpid()


def _setting(name, default, minimum=1):
    value = int(os.environ.get("BENCHFORGE_DB_" + name, default))
    if value < minimum:
        raise ValueError(f"BENCHFORGE_DB_{name} must be at least {minimum}")
    return value


def _create(url):
    options = make_url(url).query.get("options", "")
    return create_engine(
        url, pool_pre_ping=True, pool_size=_setting("POOL_SIZE", 5),
        max_overflow=_setting("MAX_OVERFLOW", 5, 0),
        pool_timeout=_setting("POOL_TIMEOUT", 10), pool_recycle=1800,
        connect_args={"connect_timeout": _setting("CONNECT_TIMEOUT", 5),
                      "application_name": "benchforge",
                      "options": options + " -c lock_timeout=5000 -c statement_timeout=30000 "
                                 "-c idle_in_transaction_session_timeout=30000"},
        hide_parameters=True,
    )


def engine_for(url=None):
    """Acquire a process pool, never one pool per request or workspace."""
    global _pid
    value = resolve_database_url(database_url=url)
    with _lock:
        if _pid != os.getpid():
            for engine in _engines.values():
                engine.dispose(close=False)
            _engines.clear()
            _pid = os.getpid()
        engine = _engines.pop(value, None)
        if engine is None:
            engine = _create(value)
        _engines[value] = engine
        while len(_engines) > 8:
            _, retired = _engines.popitem(last=False)
            retired.dispose()
        return engine


def dispose_engines():
    """Release pools at process shutdown or from isolated test fixtures."""
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()


atexit.register(dispose_engines)


def workspace_id(workspace):
    """Return a stable opaque namespace for per-workspace rows in shared tables."""
    canonical = os.path.normcase(str(Path(workspace).expanduser().resolve()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
