"""Send annotation requests to the configured OpenAI endpoint."""
import json
import os
from urllib.parse import urlsplit
import urllib.request

from service_access import configuration as provider_config


# 按标注角色的模型配置发送请求，校验凭据并返回结构化标注结果。
def annotation(args, config):
    config = provider_config.selected_model(config, 'annotation')
    if not config.get('url'):
        raise ValueError('Configure the annotation model in the selected language model provider roles.annotation')
    base_url = provider_config.llm_base_url(config['url'])
    headers = {'Content-Type': 'application/json'}
    if token_env := config.get('token_env'):
        token = os.environ.get(token_env)
        if not token:
            raise ValueError(f'Annotation model token_env references unset {token_env}')
        headers['Authorization'] = f'Bearer {token}'
    if args.get('action') == 'models':
        request = urllib.request.Request(base_url + '/models', headers=headers)
    else:
        defaults = {'reasoning_effort': config['reasoning_effort']} if config.get('reasoning_effort') else {}
        data = json.dumps({**defaults, **args.get('payload', {}), 'model': config['model']}).encode('utf8')
        request = urllib.request.Request(base_url + '/chat/completions', data=data, headers=headers)
    local = urlsplit(base_url).hostname in ('localhost', '127.0.0.1', '::1')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if local else urllib.request.build_opener()
    with opener.open(request, timeout=args.get('timeout_seconds', 300)) as response:
        return {'service_result': json.load(response), 'evidence_kind': 'model_prediction', 'gt_written': False}
