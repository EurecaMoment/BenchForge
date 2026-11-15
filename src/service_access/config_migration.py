"""One-way deployment migration; runtime code accepts only canonical settings."""
from copy import deepcopy


def migrate_configuration(value):
    result = deepcopy(value)
    version = result.get('config_version')
    if not result:
        return result
    if version not in (4, 5):
        raise ValueError('BenchForge configuration must use config_version 4 or 5')
    if version == 5:
        return result
    providers = result.setdefault('providers', {})
    former = providers.pop('IssacSim4A100', None)
    if former is not None:
        if 'IsaacSim4A100' in providers and providers['IsaacSim4A100'] != former:
            raise ValueError('Conflicting Isaac Sim provider settings; merge the canonical provider explicitly')
        providers['IsaacSim4A100'] = former
    isaac = providers.get('IsaacSim4A100', {})
    if isaac.get('mode', 'grpc') != 'grpc':
        raise ValueError('Configure the A100 gRPC endpoint before migrating a local simulator deployment')
    isaac.pop('mode', None)
    isaac.pop('local', None)
    result.pop('worker', None)
    for section in ('server', 'client'):
        for key in ('worker_token', 'worker_token_env'):
            result.get(section, {}).pop(key, None)
    result['config_version'] = 5
    return result
