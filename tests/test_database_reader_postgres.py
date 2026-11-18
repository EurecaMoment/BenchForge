"""Actual restricted reader creation, login, repeated startup and write denial."""

import secrets
import unittest
import uuid

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from tests.database_fixtures import use_postgres
from database.engine import engine_for
from database.reader import provision_reader
from database.schema import workspaces


class ReaderProvisionPostgresTests(unittest.TestCase):
    def test_reader_provision_is_idempotent_and_rejects_writes(self):
        url = use_postgres(self)
        owner = engine_for(url)
        with owner.connect() as connection:
            if not connection.execute(text('SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname=current_user')).scalar_one():
                self.skipTest('Disposable PostgreSQL test user needs CREATEROLE')
        role = 'bf_reader_test_' + uuid.uuid4().hex
        query_url = make_url(url).set(username=role, password=secrets.token_urlsafe(32)).render_as_string(hide_password=False)

        def cleanup():
            engine_for(query_url).dispose()
            with owner.begin() as connection:
                connection.exec_driver_sql(f'DROP OWNED BY "{role}"')
                connection.exec_driver_sql(f'DROP ROLE IF EXISTS "{role}"')
        self.addCleanup(cleanup)
        first = provision_reader(url, query_url, create=True)
        self.assertEqual(provision_reader(url, query_url, create=True), first)
        reader = engine_for(query_url)
        with reader.connect() as connection:
            self.assertEqual(connection.execute(select(workspaces)).all(), [])
            flags = connection.execute(text('SELECT rolsuper,rolcreaterole,rolcreatedb,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user')).first()
            self.assertFalse(any(flags))
        with self.assertRaises(DBAPIError):
            with reader.begin() as connection:
                connection.execute(workspaces.insert().values(
                    workspace_id='bad', host_id='bad', owner_id='bad', root_path='/bad',
                    root_generation=1, policy_epoch=1, state='active'))
