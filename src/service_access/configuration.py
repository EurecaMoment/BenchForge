"""Resolve the provider settings used by DSH and the production service."""

import re
from ipaddress import IPv6Address
from urllib.parse import urlsplit


MODEL_PROVIDERS = {
    'diffusion': 'FastFLUX2Klein',
    'sam3': 'FastSAM3',
    'depth': 'FastDA3',
    'trellis': 'FastTrellis2',
}
LLM_PROVIDER = 'DSpark4A100'
LLM_PROVIDERS = (LLM_PROVIDER, 'FastQwen')
ISAAC_PROVIDER = 'IsaacSim4A100'
MODEL_FIELDS = {
    'url', 'model', 'token_env', 'name', 'api', 'context_window', 'max_tokens',
    'input', 'reasoning_efforts', 'reasoning_effort', 'compat', 'retries',
}
HTTP_FIELDS = {'url', 'model', 'token_env', 'timeout_seconds', 'request_defaults'}


# 选择唯一配置了主模型的语言模型提供方，多主提供方同时启用时报错。
def llm_provider_name(config):
    providers = config.get('providers', {})
    active = [name for name in LLM_PROVIDERS if providers.get(name, {}).get('model')]
    if len(active) > 1:
        raise ValueError('Configure one primary language model provider: DSpark4A100 or FastQwen; keep other model overrides in roles')
    return active[0] if active else LLM_PROVIDER


# 合并主模型设置与指定角色覆盖，返回独立配置对象。
def selected_model(config, role):
    spec = dict(config.get('providers', {}).get(llm_provider_name(config), {}))
    roles = spec.pop('roles', {})
    return {**spec, **roles.get(role, {})}


# 去除聊天完成端点后缀，得到兼容接口的基础地址。
def llm_base_url(url):
    return url.rstrip('/').removesuffix('/chat/completions')


# 按工具与模型提供方映射取得对应服务配置。
def model_settings(config, tool):
    return config.get('providers', {}).get(MODEL_PROVIDERS[tool], {})


# 取得规范 Isaac Sim 提供方的设置，未配置时返回空对象。
def isaac_config(config):
    return config.get('providers', {}).get(ISAAC_PROVIDER, {})


# 校验 gRPC 主机与端口并规范 IPv6 地址，返回可连接的端点。
def isaac_endpoint(grpc):
    host = grpc.get('host')
    if (not isinstance(host, str) or not host or host.upper().startswith('YOUR_')
            or any(char.isspace() for char in host) or '/' in host):
        raise ValueError(f'Configure providers.{ISAAC_PROVIDER}.grpc.host in config/benchforge.local.json')
    port = grpc.get('port', 50051)
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError('The Isaac Sim gRPC port must be an integer between 1 and 65535')
    if ':' in host:
        host = host.strip('[]')
        try:
            IPv6Address(host)
        except ValueError:
            raise ValueError('The Isaac Sim gRPC host must not include the port') from None
        return f'[{host}]:{port}'
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', host):
        raise ValueError('The Isaac Sim gRPC host must be a host name or IP address')
    return f'{host}:{port}'


# 读取原生采集超时，未指定时使用默认秒数。
def capture_timeout(isaac):
    return isaac.get('capture_timeout_seconds', 1800)


# 校验绝对 HTTP 服务基础地址，拒绝凭据、查询参数及片段。
def _configured_url(value, field):
    try:
        parts = urlsplit(value) if isinstance(value, str) else None
        if (parts and parts.scheme in ('http', 'https') and parts.hostname
                and not parts.query and not parts.fragment and parts.username is None
                and not any(char.isspace() for char in value) and parts.port != 0):
            return
    except ValueError:
        pass
    raise ValueError(f'{field} must be an absolute HTTP or HTTPS base URL without credentials')


# 校验已知模型与仿真提供方的配置结构、端点、模型及允许字段。
def validate_providers(config):
    providers = config['providers']
    if not isinstance(providers, dict):
        raise ValueError('providers must be a JSON object')
    known = {*LLM_PROVIDERS, ISAAC_PROVIDER, *MODEL_PROVIDERS.values()}
    if unknown := providers.keys() - known:
        raise ValueError(f'Unknown providers: {", ".join(sorted(unknown))}')
    for name, spec in providers.items():
        if not isinstance(spec, dict):
            raise ValueError(f'providers.{name} must be a JSON object')
        if name in MODEL_PROVIDERS.values():
            field = f'providers.{name}'
            _validate_fields(spec, field, HTTP_FIELDS)
            _validate_token(spec, field)
            if spec.get('url') is not None:
                _configured_url(spec['url'], field + '.url')
                if not isinstance(spec.get('model'), str) or not spec['model'].strip():
                    raise ValueError(f'{field}.model must be the deployed model ID')
            if not isinstance(spec.get('request_defaults', {}), dict):
                raise ValueError(f'{field}.request_defaults must be a JSON object')
            timeout = spec.get('timeout_seconds', 180)
            if (type(timeout) not in (int, float) or not 0 < timeout < float('inf')
                    or (name == 'FastTrellis2' and not .01 <= timeout <= 3600)):
                raise ValueError(f'{field}.timeout_seconds is outside the supported range')
    provider = llm_provider_name(config)
    llm = providers.get(provider, {})
    roles = llm.get('roles', {})
    if not isinstance(roles, dict) or roles.keys() - {'dsh', 'scene', 'annotation'}:
        raise ValueError(f'providers.{provider}.roles must contain DSH, scene or annotation settings')
    for role, spec in (('', llm), *roles.items()):
        field = f'providers.{provider}' + (f'.roles.{role}' if role else '')
        if not isinstance(spec, dict):
            raise ValueError(f'{field} must be a JSON object')
        _validate_token(spec, field)
        _validate_fields(spec, field, MODEL_FIELDS if role else MODEL_FIELDS | {'roles'})
        spec = selected_model(config, role) if role else spec
        model = spec.get('model')
        if spec.get('url') is not None or model is not None:
            _configured_url(spec.get('url'), field + '.url')
            if not isinstance(model, str) or not model.strip() or model.upper().startswith('YOUR_'):
                raise ValueError(f'{field}.model must be the deployed model ID')
            if 'input' in spec and (not isinstance(spec['input'], list)
                    or any(item not in ('text', 'image') for item in spec['input'])):
                raise ValueError(f'{field}.input must be a list of text or image modalities')
    isaac = isaac_config(config)
    field = f'providers.{ISAAC_PROVIDER}'
    _validate_fields(isaac, field, {
        'grpc', 'capture_timeout_seconds', 'max_concurrent_captures',
        'simulation', 'render', 'artifacts', 'mesh_assets',
    })
    for key, low, high, default in (
            ('max_concurrent_captures', 1, 32, 4),):
        value = isaac.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{field}.{key} must be an integer in {low}..{high}')
    simulation = isaac.get('simulation', {})
    if not isinstance(simulation, dict):
        raise ValueError(f'{field}.simulation must be a JSON object')
    _validate_fields(simulation, field + '.simulation',
                     {'steps', 'dt', 'gravity', 'ground',
                      'capture_target_frames', 'capture_every_steps'})
    for key, low, high, default in (
            ('steps', 1, 1200, 180),
            ('capture_target_frames', 1, 1200, 24)):
        value = simulation.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{field}.simulation.{key} must be an integer in {low}..{high}')
    dt = simulation.get('dt', 1.0 / 60.0)
    if type(dt) not in (int, float) or not 0.0001 <= float(dt) <= 0.02:
        raise ValueError(f'{field}.simulation.dt must be between 0.0001 and 0.02 seconds')
    gravity = simulation.get('gravity', 9.81)
    if type(gravity) not in (int, float) or not -50 <= float(gravity) <= 50:
        raise ValueError(f'{field}.simulation.gravity is outside a reasonable range (-50..50 m/s^2)')
    ground = simulation.get('ground', True)
    if not isinstance(ground, bool):
        raise ValueError(f'{field}.simulation.ground must be a boolean')
    every = simulation.get('capture_every_steps')
    if every is not None and (type(every) is not int or every < 1):
        raise ValueError(f'{field}.simulation.capture_every_steps must be a positive integer or null')
    render = isaac.get('render', {})
    if not isinstance(render, dict):
        raise ValueError(f'{field}.render must be a JSON object')
    _validate_fields(render, field + '.render',
                     {'image_size', 'samples_per_pixel', 'fov_degrees', 'seed'})
    for key, low, high, default in (
            ('image_size', 64, 1024, 768),
            ('samples_per_pixel', 1, 256, 32),
            ('fov_degrees', 1, 179, 50)):
        value = render.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{field}.render.{key} must be an integer in {low}..{high}')
    seed = render.get('seed')
    if seed is not None and type(seed) not in (int, float):
        raise ValueError(f'{field}.render.seed must be a number or null')
    artifacts = isaac.get('artifacts', {})
    if not isinstance(artifacts, dict):
        raise ValueError(f'{field}.artifacts must be a JSON object')
    _validate_fields(artifacts, field + '.artifacts', {'chunk_bytes', 'concurrency'})
    chunk_bytes = artifacts.get('chunk_bytes', 2 * 1024 * 1024)
    if type(chunk_bytes) is not int or not 1 <= chunk_bytes <= 4 * 1024 * 1024:
        raise ValueError(f'{field}.artifacts.chunk_bytes must be an integer 1..4194304')
    concurrency = artifacts.get('concurrency', 4)
    if type(concurrency) is not int or not 1 <= concurrency <= 32:
        raise ValueError(f'{field}.artifacts.concurrency must be an integer 1..32')
    mesh_assets = isaac.get('mesh_assets', {})
    if not isinstance(mesh_assets, dict):
        raise ValueError(f'{field}.mesh_assets must map conversation asset_id to absolute server paths')
    for identity, path in mesh_assets.items():
        if not isinstance(identity, str) or not identity or not re.fullmatch(r'[A-Za-z0-9_.:-]+', identity):
            raise ValueError(f'{field}.mesh_assets key {identity!r} must be a valid asset_id string')
        if not isinstance(path, str) or not path.startswith('/'):
            raise ValueError(f'{field}.mesh_assets.{identity} must be an absolute server path')
    grpc = isaac.get('grpc', {})
    if not isinstance(grpc, dict):
        raise ValueError(f'providers.{ISAAC_PROVIDER}.grpc must be a JSON object')
    _validate_fields(grpc, f'providers.{ISAAC_PROVIDER}.grpc', {'host', 'port', 'timeout_seconds', 'token_env', 'tls_ca_file'})
    _validate_token(grpc, f'providers.{ISAAC_PROVIDER}.grpc')
    if grpc.get('host') is not None:
        isaac_endpoint(grpc)
    if 'timeout_seconds' in grpc and (type(grpc['timeout_seconds']) is not int or grpc['timeout_seconds'] <= 0):
        raise ValueError(f'providers.{ISAAC_PROVIDER}.grpc.timeout_seconds must be a positive integer')
    timeout = capture_timeout(isaac)
    if type(timeout) not in (int, float) or not 0 < timeout < float('inf'):
        raise ValueError(f'providers.{ISAAC_PROVIDER}.capture_timeout_seconds must be a positive number')


# 拒绝配置对象中超出指定允许集合的字段，指出出错配置位置。
def _validate_fields(config, name, fields):
    if unknown := config.keys() - fields:
        raise ValueError(f'{name} has unknown settings: {", ".join(sorted(unknown))}')


# 要求凭据通过合法环境变量名引用，拒绝直接嵌入令牌或 API 密钥。
def _validate_token(config, name):
    token = config.get('token_env')
    if token is not None and (not isinstance(token, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', token)):
        raise ValueError(f'{name}.token_env must name an environment variable')
    if 'token' in config or 'api_key' in config:
        raise ValueError(f'Configure {name}.token_env for credentials')
