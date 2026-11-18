"""Versioned PostgreSQL schema initialization."""

from threading import RLock
from weakref import WeakSet
from sqlalchemy import BigInteger, Column, Integer, MetaData, Table, inspect, select, text

from database.engine import engine_for
from database.schema import metadata


versions = Table(
    "benchforge_schema_versions",
    MetaData(),
    Column("version", Integer, primary_key=True),
    Column("applied_at", BigInteger, nullable=False),
)
_initialized = WeakSet()
_initialization_lock = RLock()
SCHEMA_VERSION = 4


def initialize_schema(engine=None):
    engine = engine or engine_for()
    with _initialization_lock:
        if engine in _initialized:
            return
        with engine.connect() as connection:
            tables = set(inspect(connection).get_table_names())
            if "benchforge_schema_versions" in tables:
                current = connection.execute(select(versions.c.version)
                                             .order_by(versions.c.version.desc())).scalar()
                if current and current > SCHEMA_VERSION:
                    raise RuntimeError("Database schema is newer than this BenchForge version")
                if current == SCHEMA_VERSION:
                    _initialized.add(engine)
                    return
        with engine.begin() as connection:
            connection.execute(text("SELECT pg_advisory_xact_lock(8247193006)"))
            versions.metadata.create_all(connection)
            current = connection.execute(select(versions.c.version)
                                         .order_by(versions.c.version.desc())).scalar() or 0
            if current > SCHEMA_VERSION:
                raise RuntimeError("Database schema is newer than this BenchForge version")
            for table in metadata.sorted_tables:
                table.create(connection, checkfirst=True)
            if current < 2:
                for index in metadata.tables["operations"].indexes:
                    index.create(connection, checkfirst=True)
            from time import time
            for version in range(current + 1, SCHEMA_VERSION + 1):
                connection.execute(versions.insert().values(version=version, applied_at=int(time())))
        _initialized.add(engine)
