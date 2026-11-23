"""Model-sized compaction defaults for the BenchForge preset only."""
from copy import deepcopy


# 遍历预设内的分组，定位原生压缩插件，不介入第三方引擎实现。
def _compactions(plugins):
    for plugin in plugins:
        if plugin.get('name') == '@deepseek-ai/dsh-compaction-basic':
            yield plugin
        options = plugin.get('config')
        if isinstance(options, list):
            yield from _compactions(options)
        elif isinstance(options, dict) and isinstance(options.get('plugins'), list):
            yield from _compactions(options['plugins'])


# 按模型的实际输入余量分配摘要预算，保留主模型的上下文和回答上限。
def _model_defaults(patches):
    for patch in patches:
        if patch.get('id') != 'llm-pi-ai':
            continue
        provider = patch.get('config', {}).get('providers', {}).get('benchforge-models', {})
        for model in provider.get('models', []):
            window, output = model.get('contextWindow'), model.get('maxTokens')
            if (type(window) is not int or type(output) is not int
                    or not 0 < output < window or not model.get('id')):
                continue
            headroom = min(32_768, max(1, (window - output) // 4))
            yield {'provider': 'benchforge-models', 'model': model['id'],
                   'headroomTokens': headroom, 'maxTokens': min(16_384, headroom, output)}


# 为指定模型补齐缺省字段，显式的模型策略和插件全局选项优先。
def _fill_policy(options, defaults):
    policies = options.setdefault('modelPolicies', [])
    if not isinstance(policies, list):
        return
    policy = next((row for row in policies if isinstance(row, dict)
                   and (row.get('provider'), row.get('model')) == (defaults['provider'], defaults['model'])), None)
    if policy is None:
        policy = {key: defaults[key] for key in ('provider', 'model')}
        policies.append(policy)
    for field in ('headroomTokens', 'maxTokens'):
        policy.setdefault(field, options.get(field, defaults[field]))


# 在生成与模型配置刷新时应用相同策略，只影响已明确配置模型的 BenchForge 预设。
def configure_context_policy(profile, model_patches):
    defaults = list(_model_defaults(model_patches))
    if not defaults:
        return
    for patch in profile:
        for row in patch.get('insert', []):
            preset = row.get('config', {})
            if not isinstance(preset, dict) or preset.get('id') != 'benchforge':
                continue
            for plugin in _compactions(preset.get('plugins', [])):
                options = plugin.setdefault('config', {})
                if isinstance(options, dict):
                    for policy in defaults:
                        _fill_policy(options, policy)


# 升级预设分组时保留操作者的压缩设置，再补入新生成的模型缺省策略。
def preserve_context_overrides(incoming, previous):
    existing = list(_compactions(previous))
    for plugin in _compactions(incoming):
        old = next((row for row in existing if row.get('id') == plugin.get('id')), None)
        if not old or not isinstance(old.get('config'), dict):
            continue
        defaults = plugin.get('config', {})
        if not isinstance(defaults, dict):
            continue
        options = {**deepcopy(defaults), **deepcopy(old['config'])}
        options['modelPolicies'] = deepcopy(old['config'].get('modelPolicies', []))
        for policy in defaults.get('modelPolicies', []):
            _fill_policy(options, policy)
        plugin['config'] = options
