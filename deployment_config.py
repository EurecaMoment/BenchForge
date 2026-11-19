"""Migrate and project the operator's BenchForge deployment configuration."""

from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path, PureWindowsPath
import re
import secrets
import shlex
import tempfile
import base64
from urllib.parse import quote, urlsplit, urlunsplit

from service_access.configuration import validate_providers
from service_access.runtime_config import expand_environment, read_json


SECTIONS = ('web', 'server', 'database', 'client')
SERVER_PATHS = ('artifacts', 'harness', 'task_root', 'scene_policy_path')
CLIENT_PATHS = (*SERVER_PATHS, 'models_config', 'service_config')
CONFIG_VERSION = 5
DEPLOYMENT_DEFAULTS = {
    'web': {'port': 3081, 'default_workspace': None},
    'server': {
        'service_host': '127.0.0.1', 'port': 3841, 'execution_mode': 'production',
        'artifacts': '../.benchforge/backend/artifacts',
        'scene_policy_path': 'generated/scene-library/policy.json',
    },
    'database': {'project': 'benchforge', 'port': 55432, 'user': 'benchforge', 'name': 'benchforge'},
    'client': {},
}
SERVER_CONFIG = 'benchforge-server.local.json'
CLIENT_CONFIG = 'benchforge-client.local.json'
MANAGED_MARKER = 'BENCHFORGE_DATABASE_MANAGED'
MANAGED_URL = 'BENCHFORGE_DATABASE_URL'
READER_URL = 'BENCHFORGE_READER_DATABASE_URL'
DATABASE_SETTINGS = ('COMPOSE_PROJECT_NAME', 'POSTGRES_PORT', 'POSTGRES_USER', 'POSTGRES_DB', 'POSTGRES_PASSWORD')
DEFAULT_POLICY = {
    'enabled': True,
    'enforce_realism': True,
    'require_scene_brief': True,
    'excluded_tasks': [],
    'excluded_runs': [],
    'excluded_scene_groups': [],
    'excluded_paths': [],
}
API_NAMES = ('operator',)


# 读取 JSON 对象配置；可选文件缺失时返回空对象，非对象正文时报错。
def read_object(path, *, optional=False):
    if optional and not path.exists():
        return {}
    value = read_json(path)
    if not isinstance(value, dict):
        raise ValueError(f'{path.name} must contain a JSON object')
    return value


# 只解析字面的环境赋值语句，不执行 shell；可兼容受支持的引号形式。
def read_env(path, *, shell=False):
    """Read literal .env assignments without running a shell."""
    values = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        name, separator, value = line.lstrip().removeprefix('export ').partition('=')
        name = name.strip()
        if not separator or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise ValueError(f'{path.name} contains an invalid environment assignment')
        if shell:
            # shlex preserves literal single-quoted values and supports double
            # quotes without evaluating commands or substituting environment.
            lexer = shlex.shlex(value, posix=True)
            lexer.whitespace_split = True
            try:
                words = list(lexer)
            except ValueError:
                raise ValueError(f'{path.name} has invalid quoting for {name}') from None
            value = ' '.join(words)
        values[name] = value
    return values


# 通过同目录临时文件原子替换本地配置及含密钥投影，失败时清理临时文件。
def write_text(path, value):
    """Replace local settings and secret-bearing projections atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
            stream.write(value)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


# 以保留中文且禁止非有限数的 JSON 格式原子写入配置。
def write(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n')


# 跨平台串行配置同步，避免并发初始化选出不同凭据。
@contextmanager
def projection_lock(generated):
    """Keep two init/sync processes from selecting different credentials."""
    generated.mkdir(parents=True, exist_ok=True)
    with (generated / '.sync.lock').open('a+b', buffering=0) as marker:
        if os.name == 'nt':
            import msvcrt

            marker.seek(0)
            msvcrt.locking(marker.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                marker.seek(0)
                msvcrt.locking(marker.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(marker, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(marker, fcntl.LOCK_UN)


# 接受数字或纯数字文本并校验合法 TCP 端口范围。
def port(value, field):
    if isinstance(value, str) and re.fullmatch(r'[0-9]+', value):
        value = int(value)
    if type(value) is not int or not 1 <= value <= 65535:
        raise ValueError(f'{field} must be an integer between 1 and 65535')
    return value


# 校验 Docker Compose 项目名称，防止无效部署身份进入命令参数。
def compose_name(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]*', value):
        raise ValueError('database.project must be a valid Docker Compose project name')
    return value


# 校验设置指定的是合法环境变量名。
def env_name(value, field):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', value):
        raise ValueError(f'{field} must name an environment variable')
    return value


# 要求非空单行文本，拒绝换行和空字符进入配置投影。
def text_value(value, field):
    if not isinstance(value, str) or not value or any(c in value for c in '\r\n\0'):
        raise ValueError(f'{field} must be a nonempty string without line breaks')
    return value


# 展开路径变量并相对配置目录解析，跨宿主的显式 Windows 路径保持原值。
def _file_path(value, directory, environment):
    expanded = expand_environment(value, environment)
    if os.name != 'nt' and PureWindowsPath(expanded).is_absolute():
        # Preserve explicitly configured paths belonging to a different host.
        return expanded
    path = Path(expanded).expanduser()
    return str((directory / path).resolve())


# 批量解析配置对象中已设置的路径字段。
def _resolve_paths(values, fields, directory, environment):
    for field in fields:
        if values.get(field):
            values[field] = _file_path(values[field], directory, environment)


# 返回本安装生成的服务和客户端配置的规范路径映射。
def local_service_configs(generated):
    return {path.resolve(): path for path in (generated / SERVER_CONFIG, generated / CLIENT_CONFIG)}


# 未配置外部连接地址或其环境变量引用时启用托管数据库。
def database_is_managed(database):
    return not (database.get('url') or database.get('url_env'))


# 从上一次数据库投影恢复 Compose 项目名，供安全停止旧实例使用。
def previous_database_project(saved):
    return saved.get('COMPOSE_PROJECT_NAME', 'benchforge') if saved else None


# 提取允许的部署分区并要求每个分区均为 JSON 对象。
def _sections(local):
    result = {}
    for name in SECTIONS:
        value = local.get(name, {})
        if not isinstance(value, dict):
            raise ValueError(f'{name} configuration must be a JSON object')
        result[name] = value
    return result


# 递归补齐缺失默认项，保留本地已显式设置的值。
def _inherit_defaults(values, defaults):
    for key, value in defaults.items():
        if key not in values:
            values[key] = deepcopy(value)
        elif isinstance(values[key], dict) and isinstance(value, dict):
            _inherit_defaults(values[key], value)


# 加载模板与本地配置，迁移版本、应用显式覆盖并校验部署分区和提供方。
def read_configuration(config, *, overrides=None):
    template = read_object(config / 'benchforge.example.json')
    path = config / 'benchforge.local.json'
    original = read_object(path, optional=True)
    from service_access.config_migration import migrate_configuration
    local = migrate_configuration(original)
    known = {'config_version', 'dsh', 'providers', 'collectors', 'cleaning', *SECTIONS}
    if unknown := local.keys() - known:
        raise ValueError(f'Unknown BenchForge configuration sections: {", ".join(sorted(unknown))}')
    environment = {**read_env(config / 'dsh.env', shell=True), **os.environ}
    registry = deepcopy(local)
    settings = _sections(registry)
    for name in SECTIONS:
        _inherit_defaults(settings[name], (overrides or {}).get(name, {}))
        _inherit_defaults(settings[name], DEPLOYMENT_DEFAULTS[name])
        registry[name] = settings[name]
    providers = registry.setdefault('providers', {})
    if isinstance(providers, dict):
        _inherit_defaults(providers, template['providers'])
    for name in ('web', 'server', 'database'):
        settings[name]['port'] = port(settings[name]['port'], f'{name}.port')
    default_workspace = settings['web']['default_workspace']
    if default_workspace is not None:
        text_value(default_workspace, 'web.default_workspace')
    settings['database']['project'] = compose_name(settings['database']['project'])
    mode = settings['server']['execution_mode']
    if mode not in ('production', 'exploration'):
        raise ValueError('server.execution_mode must be production or exploration')
    registry['config_version'] = CONFIG_VERSION
    registry['dsh'] = template['dsh']
    validate_providers(registry)
    return registry, original, environment


# 复用已有凭据；仅在缺失时生成指定随机强度的 URL 安全文本。
def _secret(credentials, field, size):
    if not credentials.get(field):
        credentials[field] = secrets.token_urlsafe(size)
    return text_value(credentials[field], field)


# 优先保留环境变量形式的令牌配置，否则复用或生成本地凭据并移除冲突字段。
def _token(config, name, credentials):
    field = name + '_token_env'
    if config.get(field):
        env_name(config[field], 'server.' + field)
        config.pop(name + '_token', None)
    else:
        config.pop(field, None)
        config[name + '_token'] = _secret(credentials, name + '_token', 32)


# 生成客户端可连接的服务地址，把通配监听主机映射为本机回环地址。
def _service_url(server):
    if server.get('service_url'):
        return text_value(server['service_url'], 'server.service_url')
    host = text_value(server['service_host'], 'server.service_host').strip('[]')
    if host in ('0.0.0.0', '::'):
        host = '127.0.0.1'
    if ':' in host:
        host = f'[{host}]'
    return f"http://{host}:{server['port']}"


# 解析托管或外部数据库配置，生成服务与独立读角色一致使用的环境投影。
def _database_projection(database, environment, credentials):
    name = database.get('url_env')
    url = database.get('url')
    if url and name:
        raise ValueError('Configure database.url or database.url_env, not both')
    managed = database_is_managed(database)
    resolved = {}
    env = {MANAGED_MARKER: 'true' if managed else 'false',
           'COMPOSE_PROJECT_NAME': database['project'], 'POSTGRES_PORT': str(database['port'])}
    if not managed and name:
        name = env_name(name, 'database.url_env')
        if name in {MANAGED_MARKER, *DATABASE_SETTINGS}:
            raise ValueError('database.url_env conflicts with a managed database setting')
        resolved['database_url_env'] = name
    elif not managed:
        resolved['database_url'] = text_value(url, 'database.url')
    else:
        user = text_value(database['user'], 'database.user')
        db_name = text_value(database['name'], 'database.name')
        password_env = database.get('password_env')
        if password_env is not None:
            password_env = env_name(password_env, 'database.password_env')
            password = environment.get(password_env)
            if not password:
                raise ValueError(f'database.password_env references unset variable {password_env}')
            password = text_value(password, 'database password')
        else:
            password = _secret(credentials, 'database_password', 24)
        resolved['database_url_env'] = MANAGED_URL
        env.update(POSTGRES_USER=user, POSTGRES_DB=db_name, POSTGRES_PASSWORD=password)
        env[MANAGED_URL] = (
            f'postgresql+psycopg://{quote(user, safe="")}:{quote(password, safe="")}@127.0.0.1:'
            f"{database['port']}/{quote(db_name, safe='')}"
        )
    reader_env = database.get('reader_url_env')
    if reader_env:
        resolved['reader_database_url_env'] = env_name(reader_env, 'database.reader_url_env')
        if reader_env == resolved.get('database_url_env'):
            raise ValueError('database.reader_url_env must name a separate reader connection')
        resolved['reader_provision'] = False
    else:
        if not credentials.get('database_reader_role'):
            credentials['database_reader_role'] = 'benchforge_reader_' + secrets.token_hex(8)
        role = text_value(credentials['database_reader_role'], 'reader role')
        password = _secret(credentials, 'database_reader_password', 32)
        service = env.get(MANAGED_URL) or url or environment.get(name or '')
        if not service:
            raise ValueError('Set database.url_env before generating the dedicated reader connection')
        parsed = urlsplit(service)
        if parsed.scheme not in ('postgres', 'postgresql', 'postgresql+psycopg'):
            raise ValueError('The reader requires the same PostgreSQL service database')
        authority = parsed.netloc.rsplit('@', 1)[-1]
        env[READER_URL] = urlunsplit((parsed.scheme,
            quote(role, safe='') + ':' + quote(password, safe='') + '@' + authority,
            parsed.path, parsed.query, parsed.fragment))
        resolved['reader_database_url_env'] = READER_URL
        resolved['reader_provision'] = True
    return resolved, env


# 在同步锁内校验配置、复用凭据并原子更新本地及运行投影，清理已退役设置。
def sync_configuration(config, generated, server_path, state, *, overrides=None):
    path = config / 'benchforge.local.json'
    credentials_path = state / 'credentials.local.json'
    with projection_lock(generated):
        value, previous_value, environment = read_configuration(config, overrides=overrides)
        stored_credentials = read_object(credentials_path, optional=True)
        credentials = dict(stored_credentials)
        retired = [generated / 'worker.local.json', generated / 'benchforge-worker.local.json']
        retired_keys = [key for key in credentials if 'worker' in key]
        if previous_value.get('config_version') == 4 or any(file.exists() for file in retired) or retired_keys:
            backup = state / 'configuration-before-v5.json'
            if not backup.exists():
                write(backup, previous_value)
            for file in [credentials_path, *retired]:
                if file.is_file():
                    target = state / 'retired-capture-config' / (file.name + '.json')
                    if not target.exists():
                        write(target, {'original_path': str(file),
                                       'content_base64': base64.b64encode(file.read_bytes()).decode('ascii')})
            for key in retired_keys:
                credentials.pop(key)
        for scope, fields in (
            ('server', {'operator_token': 'operator_token'}),
            ('client', {'operator_token': 'client_operator_token'}),
        ):
            for field, key in fields.items():
                token = value[scope].pop(field, None)
                if token is not None:
                    credentials[key] = text_value(token, scope + '.' + field)
                    if scope != 'server' and not value[scope].get(field + '_env'):
                        value[scope][field + '_env'] = 'BENCHFORGE_' + key.upper()
        server = dict(value['server'])
        if server.get('scene_policy_path') is None and server['execution_mode'] == 'production':
            raise ValueError('server.scene_policy_path is required in production mode')
        _resolve_paths(server, SERVER_PATHS, config, environment)
        if 'task_root' not in server:
            server['task_root'] = str(Path(server['artifacts']) / 'operator_workspaces')
        server['service_url'] = _service_url(server)
        for name in API_NAMES:
            _token(server, name, credentials)
        server['models_config'] = Path(os.path.relpath(path, server_path.parent)).as_posix()
        db_file = generated / 'database.env'
        database, database_env = _database_projection(value['database'], environment, credentials)
        server.update(database)

        client = dict(value['client'])
        _resolve_paths(client, CLIENT_PATHS, config, environment)
        if not (client.get('service_config') or client.get('service_url')):
            client['service_config'] = Path(os.path.relpath(server_path, generated)).as_posix()
        if (client.get('operator_token_env') == 'BENCHFORGE_CLIENT_OPERATOR_TOKEN'
                and credentials.get('client_operator_token')):
            client.pop('operator_token_env')
            client['operator_token'] = credentials['client_operator_token']

        policy = server.get('scene_policy_path')
        default_policy = generated / 'scene-library/policy.json'
        if policy and Path(policy) == default_policy.resolve() and not default_policy.exists():
            write(default_policy, DEFAULT_POLICY)
        if credentials and credentials != stored_credentials:
            write(credentials_path, credentials)
        write(server_path, server)
        write(generated / CLIENT_CONFIG, client)
        write_text(db_file, ''.join(f'{key}={item}\n' for key, item in database_env.items()))
        saved = {
            key: item for key, item in value.items() if key not in SECTIONS
        }
        for name in SECTIONS:
            previous_section = previous_value.get(name, {})
            section = {
                key: item for key, item in value[name].items()
                if key in previous_section
                or key in (overrides or {}).get(name, {})
                or item != DEPLOYMENT_DEFAULTS[name].get(key)
            }
            if section and name == 'server':
                for key in ('operator_token_env',):
                    if section.get(key) is None:
                        section.pop(key, None)
            if section and name == 'database':
                for key in ('url', 'url_env', 'password_env'):
                    if section.get(key) is None:
                        section.pop(key, None)
            if section:
                saved[name] = section
        if saved != previous_value or not path.exists():
            if path.exists() and previous_value.get('config_version') == 4:
                backup = state / 'configuration-before-v5.json'
                if not backup.exists():
                    write(backup, previous_value)
            write(path, saved)
        for file in retired:
            file.unlink(missing_ok=True)
    return value
