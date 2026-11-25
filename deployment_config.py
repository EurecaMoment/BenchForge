        server = dict(value['server'])
        isaac = value.get('providers', {}).get('IsaacSim4A100')
        server_providers = server.get('providers')
        if not isinstance(server_providers, dict):
            server_providers = {}
        else:
            server_providers = dict(server_providers)
        if isinstance(isaac, dict) and isaac:
            server_providers['IsaacSim4A100'] = deepcopy(isaac)
        else:
            server_providers.pop('IsaacSim4A100', None)
        if server_providers:
            server['providers'] = server_providers
        else:
            server.pop('providers', None)