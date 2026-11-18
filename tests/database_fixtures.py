"""Disposable PostgreSQL schemas scoped to individual regression tests."""

import os
import uuid
from unittest.mock import patch

from sqlalchemy import text
from sqlalchemy.engine import make_url
from database.engine import dispose_engines, engine_for
from database.migrations import initialize_schema


def use_postgres(test):
    """Never use deployment credentials; each test gets an isolated schema."""
    url = os.environ.get('BENCHFORGE_TEST_POSTGRES_URL')
    if not url:
        test.skipTest('Set BENCHFORGE_TEST_POSTGRES_URL to a disposable PostgreSQL database')
    admin = engine_for(url)
    schema = 'test_' + uuid.uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    database_url = make_url(url).update_query_dict({'options': f'-c search_path={schema}'}).render_as_string(hide_password=False)
    def drop():
        dispose_engines()
        with engine_for(url).begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    test.addCleanup(drop)
    environment = patch.dict(os.environ, {
        'BENCHFORGE_DATABASE_URL': database_url, 'BENCHFORGE_EXECUTION_MODE': 'exploration',
    })
    environment.start()
    test.addCleanup(environment.stop)
    os.environ.pop('BENCHFORGE_SERVICE_CONFIG', None)
    initialize_schema(engine_for(database_url))
    return database_url
