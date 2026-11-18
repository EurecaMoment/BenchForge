"""Short transactions, entity-scoped locks and atomic conflict handling."""

from contextlib import contextmanager
import hashlib
import json

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as postgres_insert


@contextmanager
def transaction(engine):
    with engine.connect() as connection:
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def lock_entity(connection, domain, *identity):
    data = json.dumps([domain, *identity], separators=(",", ":")).encode()
    key = int.from_bytes(hashlib.blake2b(data, digest_size=8).digest(), "big", signed=True)
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def insert_statement(connection, table):
    return postgres_insert(table)


def insert_missing(connection, table, values, keys):
    return connection.execute(insert_statement(connection, table).values(**values)
                              .on_conflict_do_nothing(index_elements=keys))
