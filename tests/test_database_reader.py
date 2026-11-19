"""Reader deployment configuration remains private, stable and fail-closed."""

import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

import deployment_config
from database.reader import _same_database, provision_reader, reader_url


class DatabaseReaderTests(unittest.TestCase):
    # 验证托管部署生成独立且重复同步稳定的读角色凭据。
    def test_managed_projection_generates_stable_independent_credentials(self):
        credentials = {}
        settings = dict(deployment_config.DEPLOYMENT_DEFAULTS['database'])
        first, environment = deployment_config._database_projection(settings, {}, credentials)
        second, repeated = deployment_config._database_projection(settings, {}, credentials)
        self.assertEqual((first, environment), (second, repeated))
        self.assertEqual(first['reader_database_url_env'], deployment_config.READER_URL)
        self.assertTrue(first['reader_provision'])
        service, query = map(urlsplit, (environment[deployment_config.MANAGED_URL], environment[deployment_config.READER_URL]))
        self.assertNotEqual(service.username, query.username)
        self.assertNotEqual(service.password, query.password)
        self.assertEqual((service.hostname, service.port, service.path), (query.hostname, query.port, query.path))
        self.assertNotIn('reader_database_url', first)

    # 验证外部读连接环境配置被保留，不额外生成密码。
    def test_external_reader_environment_is_preserved_without_a_new_password(self):
        credentials = {}
        settings = {**deployment_config.DEPLOYMENT_DEFAULTS['database'],
                    'url_env': 'SERVICE_URL', 'reader_url_env': 'QUERY_URL'}
        config, environment = deployment_config._database_projection(settings, {}, credentials)
        self.assertEqual(config['reader_database_url_env'], 'QUERY_URL')
        self.assertFalse(config['reader_provision'])
        self.assertNotIn(deployment_config.READER_URL, environment)
        self.assertEqual(credentials, {})

    # 验证自动生成读地址时保留原数据库及连接选项。
    def test_external_auto_reader_keeps_database_and_connection_options(self):
        settings = {**deployment_config.DEPLOYMENT_DEFAULTS['database'], 'url_env': 'SERVICE_URL'}
        _, environment = deployment_config._database_projection(settings, {
            'SERVICE_URL': 'postgresql://service:private@localhost:5444/db?sslmode=require'}, {})
        parsed = urlsplit(environment[deployment_config.READER_URL])
        self.assertEqual((parsed.hostname, parsed.port, parsed.path, parsed.query),
                         ('localhost', 5444, '/db', 'sslmode=require'))

    # 验证读角色不能复用所有者身份，也不能指向另一数据库。
    def test_reader_cannot_reuse_owner_or_another_database(self):
        for query in ('postgresql://owner:secret@localhost/db',
                      'postgresql://reader:secret@localhost/other',
                      'postgresql://reader:secret@elsewhere/db'):
            with self.subTest(query=query), self.assertRaises((ValueError, PermissionError)):
                _same_database('postgresql://owner:secret@localhost/db', query)
        with self.assertRaises(ValueError):
            reader_url({}, environ={})

    # 验证缺少建角色能力时先失败，不留下部分角色修改。
    def test_missing_create_role_fails_before_any_role_mutation(self):
        connection = MagicMock()
        engine = MagicMock()
        engine.begin.return_value.__enter__.return_value = connection
        connection.execute.return_value.scalar_one_or_none.return_value = None
        connection.execute.return_value.scalar_one.return_value = False
        with patch('database.reader.engine_for', return_value=engine), self.assertRaisesRegex(PermissionError, 'CREATEROLE'):
            provision_reader('postgresql://owner:private@localhost/db',
                             'postgresql://reader:private@localhost/db', create=True)
        connection.connection.driver_connection.cursor.assert_not_called()
