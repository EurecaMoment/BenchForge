"""Resolve runtime database and model settings on top of shared service configuration."""
import os

from service_access.configuration import selected_model
from service_access.service_config import (
    PATH_ENVIRONMENT as _PATH_ENVIRONMENT,
    environment_value as environment_value,
    expand_environment as expand_environment,
    load_common_config,
    read_json as read_json,
    resolve_path as resolve_path,
)


# 通过数据库包统一解析共享数据库地址，避免调用方自行拼接连接。
def resolve_database_url(harness=None, database_url=None):
    """Resolve the one shared database URL through the database package."""
    from database.config import resolve_database_url as resolve
    return resolve(harness, database_url)


# 读取模型配置的场景角色，并投影成运行时使用的模型端点与凭据引用。
def _resolve_model_settings(config):
    reference = config.get('models_config')
    if not reference:
        return
    spec = selected_model(read_json(reference), 'scene')
    for source, target, variable in (
        ('url', 'model_url', 'BENCHFORGE_MODEL_URL'),
        ('model', 'model_id', 'BENCHFORGE_MODEL_ID'),
        ('token_env', 'model_api_key_env', 'BENCHFORGE_MODEL_API_KEY_ENV'),
        ('reasoning_effort', 'model_reasoning_effort', 'BENCHFORGE_MODEL_REASONING_EFFORT'),
    ):
        if spec.get(source):
            config[target] = spec[source]
        else:
            config.pop(target, None)


# 加载并合并引用配置，解析路径、环境变量、模型及凭据而不改动进程环境。
def load_service_config(path, *, required_tokens=('operator',)):
    """Resolve one configuration without mutating process-wide environment."""
    config = load_common_config(path, require_operator_token='operator' in required_tokens)
    mode = config['execution_mode']
    if config.get('database_url_env'):
        env_name = expand_environment(str(config['database_url_env']))
        config['database_url_env'] = env_name
        database_url = environment_value(env_name)
        if database_url is None:
            raise ValueError(f'database_url_env references unset environment variable {env_name}')
        config['database_url'] = database_url
    elif 'database_url' in config and isinstance(config['database_url'], str):
        config['database_url'] = expand_environment(config['database_url'])
    from database.config import resolve_database_url as database_url_for
    if mode == 'production' or config.get('database_url') or environment_value('BENCHFORGE_DATABASE_URL'):
        config['database_url'] = database_url_for(config.get('harness'), config.get('database_url'))
        if mode == 'production':
            from database.config import normalize_database_url
            config['database_url'] = normalize_database_url(config['database_url'])

    _resolve_model_settings(config)

    environment_values = (
        ('model_url', 'BENCHFORGE_MODEL_URL'),
        ('model_id', 'BENCHFORGE_MODEL_ID'),
        ('model_api_key_env', 'BENCHFORGE_MODEL_API_KEY_ENV'),
        ('model_reasoning_effort', 'BENCHFORGE_MODEL_REASONING_EFFORT'),
    )
    for key, env_name in environment_values:
        value = environment_value(env_name, config.get(key))
        if value is not None:
            resolved = expand_environment(str(value))
            config[key] = resolved
    return config


# 只在服务可执行入口把已解析配置投影到兼容旧调用的环境变量。
def apply_runtime_environment(config):
    """Install one service's legacy environment at its executable startup boundary."""
    mapping = {**_PATH_ENVIRONMENT, 'execution_mode': 'BENCHFORGE_EXECUTION_MODE',
               'database_url': 'BENCHFORGE_DATABASE_URL',
               'model_url': 'BENCHFORGE_MODEL_URL', 'model_id': 'BENCHFORGE_MODEL_ID',
               'model_api_key_env': 'BENCHFORGE_MODEL_API_KEY_ENV',
               'model_reasoning_effort': 'BENCHFORGE_MODEL_REASONING_EFFORT'}
    for key, variable in mapping.items():
        if key in config:
            os.environ[variable] = str(config[key])
        else:
            os.environ.pop(variable, None)
