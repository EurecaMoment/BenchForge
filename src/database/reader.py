"""Provision and verify the deployment's dedicated scoped-query connection."""

import os

from psycopg import sql
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from database.access import assert_reader_role
from database.access_policies import grant_reader
from database.config import normalize_database_url
from database.engine import engine_for
from database.schema import workspaces


def reader_url(config, *, environ=None):
    environment = os.environ if environ is None else environ
    name = config.get('reader_database_url_env', 'BENCHFORGE_READER_DATABASE_URL')
    value = environment.get(name)
    if not value:
        raise ValueError('Configure the dedicated reader connection environment, or run benchforge_app.py sync')
    return normalize_database_url(value)


def _same_database(service, reader):
    left, right = make_url(service), make_url(reader)
    if ((left.host, left.port or 5432, left.database, dict(left.query)) !=
            (right.host, right.port or 5432, right.database, dict(right.query))):
        raise ValueError('The service and reader must address the same PostgreSQL database and schema')
    if not right.username or right.username == left.username:
        raise PermissionError('Use a separate non-owner PostgreSQL reader role')
    if len(right.username.encode('utf-8')) > 63:
        raise ValueError('PostgreSQL reader role name must not exceed 63 bytes')
    return right


def provision_reader(service_url, query_url, *, create=False):
    """Create only an absent dedicated role; never rotate another role's password."""
    query = _same_database(service_url, query_url)
    engine = engine_for(service_url)
    try:
        with engine.begin() as connection:
            connection.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:role,0))'),
                               {'role': 'benchforge-reader:' + query.username})
            exists = connection.execute(text('SELECT 1 FROM pg_roles WHERE rolname=:role'),
                                        {'role': query.username}).scalar_one_or_none()
            if not exists:
                can_create = connection.execute(text(
                    'SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname=current_user')).scalar_one()
                if not create or not can_create:
                    raise PermissionError(
                        'The dedicated reader role is missing. Automatic provisioning needs CREATEROLE; '
                        'ask the database administrator to create a restricted LOGIN role and set database.reader_url_env.')
                if not query.password:
                    raise ValueError('Automatic reader provisioning needs its generated password')
                with connection.connection.driver_connection.cursor() as cursor:
                    cursor.execute(sql.SQL(
                        'CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT '
                        'NOREPLICATION NOBYPASSRLS PASSWORD {}').format(
                            sql.Identifier(query.username), sql.Literal(query.password)))
                    for setting, value in [('default_transaction_read_only', 'on'),
                                           ('statement_timeout', '5000ms'), ('lock_timeout', '2000ms')]:
                        cursor.execute(sql.SQL('ALTER ROLE {} SET {} = {}').format(
                            sql.Identifier(query.username), sql.Identifier(setting), sql.Literal(value)))
            grant_reader(connection, query.username)
        query_engine = engine_for(query_url)
        with query_engine.connect() as connection:
            assert_reader_role(connection)
            visible = connection.execute(select(workspaces.c.workspace_id).limit(1)).first()
            if visible is not None:
                raise PermissionError('Unscoped reader connection must not expose workspace records')
    except DBAPIError:
        raise RuntimeError('Dedicated PostgreSQL reader provisioning or login failed; verify its private connection '
                           'and administrator role grants. The owner connection will not be used for model queries.') from None
    return {'status': 'ok', 'role': query.username}
