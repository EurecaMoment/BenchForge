"""Shared service configuration rules, without model or database initialization."""
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


_ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)|%([A-Za-z_][A-Za-z0-9_]*)%")
PATH_ENVIRONMENT = {
    'artifacts': 'BENCHFORGE_ARTIFACTS',
    'harness': 'BENCHFORGE_HARNESS',
    'models_config': 'BENCHFORGE_MODELS_CONFIG',
    'task_root': 'BENCHFORGE_TASK_ROOT',
    'scene_policy_path': 'BENCHFORGE_SCENE_POLICY',
}


def environment_value(name, default=None, *, environ=None):
    """从显式环境或当前进程读取变量，不写入全局环境。"""
    return (os.environ if environ is None else environ).get(name, default)


def _reject_duplicate_keys(pairs):
    """拒绝配置内重复键，避免服务和客户端得到不同含义。"""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def read_json(path):
    """读取 UTF-8 配置，并在每层对象校验键的唯一性。"""
    return json.loads(Path(path).expanduser().resolve().read_text(encoding='utf-8'),
                      object_pairs_hook=_reject_duplicate_keys)


def expand_environment(value, environ=None):
    """展开三种环境变量引用，缺少变量时报错而不留下占位符。"""
    if not isinstance(value, str):
        raise ValueError('configuration path/value must be a string')

    def replace(match):
        """按变量名取值，保留显式提供的空字符串。"""
        name = next(part for part in match.groups() if part is not None)
        resolved = environment_value(name, environ=environ)
        if resolved is None:
            raise ValueError(f'configuration references unset environment variable {name}')
        return resolved

    return _ENV_REFERENCE.sub(replace, value)


def resolve_path(value, config_path, environ=None):
    """展开用户目录和变量，再相对配置文件位置解析路径。"""
    path = Path(expand_environment(os.fspath(value), environ)).expanduser()
    return (path if path.is_absolute() else Path(config_path).parent / path).resolve()


def resolve_service_config_file(path):
    """解析显式配置文件入口，保持调用者自定义位置的兼容性。"""
    return Path(expand_environment(os.fspath(path))).expanduser().resolve()


def _document(path):
    """合并公开的单层 service_config 引用，并保留既有冲突保护规则。"""
    path = resolve_service_config_file(path)
    config = read_json(path)
    if not isinstance(config, dict):
        raise ValueError('BenchForge service configuration must be a JSON object')
    if config.get('service_config'):
        base_path = resolve_path(config['service_config'], path)
        base = read_json(base_path)
        if not isinstance(base, dict):
            raise ValueError('Referenced BenchForge service configuration must be a JSON object')
        overrides = {key: value for key, value in config.items() if key != 'service_config'}
        for field in ('port', 'service_url', 'execution_mode', 'scene_policy_path'):
            if field in overrides and field in base and overrides[field] != base[field]:
                raise ValueError(f'{field} in {path.name} conflicts with referenced service configuration')
        config, path = {**base, **overrides}, base_path
    return config, path


def _service_url(config):
    """统一服务地址优先级，保留显式主机名并校验协议与端口。"""
    value = environment_value('BENCHFORGE_SERVICE_URL') or config.get('service_url')
    if value:
        url = expand_environment(str(value)).rstrip('/')
    else:
        raw_port = config.get('port') if config.get('port') is not None else 3841
        try:
            numeric_port = float(raw_port)
            port = int(numeric_port)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError('port must be an integer') from error
        if isinstance(raw_port, bool) or numeric_port != port:
            raise ValueError('port must be an integer')
        if not 1 <= port <= 65535:
            raise ValueError('port must be between 1 and 65535')
        host = expand_environment(str(config.get('service_host') or '127.0.0.1'))
        if ':' in host and not host.startswith('['):
            host = '[' + host + ']'
        url = f'http://{host}:{port}'
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.netloc or not parsed.hostname:
        raise ValueError('service_url must be an absolute HTTP or HTTPS URL')
    parsed.port  # Accessing the port validates malformed and out-of-range URL ports.
    return url


def _operator_token(config, required):
    """按同一优先级解析操作者凭据，保持只读查询可选凭据的能力。"""
    env_name = config.get('operator_token_env')
    if env_name:
        env_name = expand_environment(str(env_name))
        token = environment_value(env_name)
        if not token and required:
            raise ValueError(f'operator_token_env references unset environment variable {env_name}')
    else:
        token = config.get('operator_token') or environment_value('BENCHFORGE_OPERATOR_TOKEN')
    if not token and required:
        raise ValueError('Configure operator_token or set BENCHFORGE_OPERATOR_TOKEN')
    return token or ''


def _execution_policy(config, path, required):
    """统一执行模式与策略路径发现，仅在生产入口要求完整场景策略。"""
    configured = config.get('execution_mode')
    environment = environment_value('BENCHFORGE_EXECUTION_MODE')
    if configured is not None and environment is not None and configured != environment:
        raise ValueError('BENCHFORGE_EXECUTION_MODE conflicts with configured execution_mode')
    mode = configured if configured is not None else environment or 'production'
    if mode not in ('production', 'exploration'):
        raise ValueError('execution_mode must be production or exploration')
    config['execution_mode'] = mode
    policy = config.get('scene_policy_path')
    if not policy:
        candidates = ((Path(config['task_root']) / 'scene-library/policy.json') if config.get('task_root') else None,
                      path.parent / 'scene-library/policy.json', path.parent.parent / 'scene-library/policy.json')
        policy = next((candidate for candidate in candidates if candidate and candidate.is_file()), None)
    if policy is not None:
        policy = Path(policy)
        config['scene_policy_path'] = str(policy)
        if required:
            if not policy.is_file():
                raise ValueError(f'scene policy file does not exist: {policy}')
            value = read_json(policy)
            if not isinstance(value, dict):
                raise ValueError('scene policy must be a JSON object')
            if mode == 'production' and (value.get('enabled') is not True or value.get('enforce_realism') is not True):
                raise ValueError('production execution requires an enabled scene policy with enforce_realism=true')
    else:
        config.pop('scene_policy_path', None)
        if required and mode == 'production':
            raise ValueError('execution_mode=production requires scene_policy_path pointing to an existing policy.json')


def load_common_config(path, *, require_operator_token=True, require_scene_policy=True):
    """集中解析配置继承、路径、环境、服务地址、凭据和执行策略。"""
    config, path = _document(path)
    for field, variable in PATH_ENVIRONMENT.items():
        value = environment_value(variable, config.get(field))
        if value is not None:
            config[field] = str(resolve_path(value, path))
    config['service_url'] = _service_url(config)
    config['operator_token'] = _operator_token(config, require_operator_token)
    _execution_policy(config, path, require_scene_policy)
    return config
