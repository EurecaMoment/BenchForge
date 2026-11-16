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


def llm_provider_name(config):
    providers = config.get('providers', {})
    active = [name for name in LLM_PROVIDERS if providers.get(name, {}).get('model')]
    if len(active) > 1:
        raise ValueError('Configure one primary language model provider: DSpark4A100 or FastQwen; keep other model overrides in roles')
    return active[0] if active else LLM_PROVIDER


def selected_model(config, role):
    spec = dict(config.get('providers', {}).get(llm_provider_name(config), {}))
    roles = spec.pop('roles', {})
    return {**spec, **roles.get(role, {})}


def llm_base_url(url):
    return url.rstrip('/').removesuffix('/chat/completions')


def model_settings(config, tool):
    return config.get('providers', {}).get(MODEL_PROVIDERS[tool], {})


def isaac_config(config):
    return config.get('providers', {}).get(ISAAC_PROVIDER, {})


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


def capture_timeout(isaac):
    return isaac.get('capture_timeout_seconds', 1800)


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
    _validate_fields(isaac, field, {'grpc', 'capture_timeout_seconds', 'max_concurrent_captures',
                                  'image_size', 'samples_per_pixel', 'asset_sources'})
    for key, low, high, default in (('max_concurrent_captures', 1, 32, 4),
                                   ('image_size', 64, 1024, 768), ('samples_per_pixel', 1, 256, 32)):
        value = isaac.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{field}.{key} must be an integer in {low}..{high}')
    bindings = isaac.get('asset_sources', {})
    if not isinstance(bindings, dict):
        raise ValueError(f'{field}.asset_sources must map native catalog IDs to uploaded A100 assets')
    for identity, binding in bindings.items():
        if not isinstance(binding, dict):
            raise ValueError('Isaac Sim asset binding must be an object')
        _validate_fields(binding, field + '.asset_sources.' + identity, {'asset_id', 'relative_path', 'fix_base'})
        if not all(isinstance(binding.get(key), str) and binding[key] for key in ('asset_id', 'relative_path')):
            raise ValueError('Isaac Sim asset binding needs asset_id and relative_path')
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


def _validate_fields(config, name, fields):
    if unknown := config.keys() - fields:
        raise ValueError(f'{name} has unknown settings: {", ".join(sorted(unknown))}')


def _validate_token(config, name):
    token = config.get('token_env')
    if token is not None and (not isinstance(token, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', token)):
        raise ValueError(f'{name}.token_env must name an environment variable')
    if 'token' in config or 'api_key' in config:
        raise ValueError(f'Configure {name}.token_env for credentials')
