"""Read durable authoring inputs without making the agent retranscribe them."""
import json
from copy import deepcopy
from pathlib import Path

FIELDS = {
    'assessment', 'assessment_path', 'objective', 'pipeline_id', 'capability',
    'capabilities', 'template_ids', 'task_ids', 'target_items', 'selections',
    'steps', 'inputs', 'evidence', 'seed', 'split', 'service_config',
    'applies_edit', 'user_markdown', 'markdown_resolution', 'new_task',
}
CONTROL_FIELDS = {'validate_only'}

REVISION_INPUTS = ('execution_mode', 'quantity_contract', 'scene_selection',
                   'preserve_source_splits', 'target_scene_count', 'seed', 'split')


# 同任务修订继承省略的生产约束与等价模板分配；显式输入仍须经过完整契约校验。
def inherit_revision_inputs(args, previous):
    if previous is None or args.get('new_task', False):
        return args, []
    value, inherited = deepcopy(args), []
    for key in ('objective', 'pipeline_id', 'user_markdown'):
        if key not in value and key in previous:
            value[key] = deepcopy(previous[key])
            inherited.append(key)
    inputs = value.setdefault('inputs', {})
    if not isinstance(inputs, dict):
        raise ValueError('inputs must be an object')
    for key in REVISION_INPUTS:
        if key not in inputs and key not in value and key in previous.get('inputs', {}):
            inputs[key] = deepcopy(previous['inputs'][key])
            inherited.append('inputs.' + key)
    selections = previous.get('selections', [])
    prior_ids = {tid for row in selections for tid in row.get('template_ids', [])}
    selectors = any(value.get(key) for key in ('capability', 'capabilities', 'task_ids'))
    template_ids = value.get('template_ids')
    same_templates = not template_ids or set(template_ids) == prior_ids
    same_count = ('target_items' not in value or
                  value['target_items'] == sum(row['count'] for row in selections))
    if ('selections' not in value and selections and not selectors and same_templates and same_count):
        value['selections'] = deepcopy(selections)
        inherited.append('selections')
    return value, inherited


# 读取工作区中的有界 JSON 请求文件，要求文件存在且正文为对象。
def _document(source, workspace, field):
    if not isinstance(source, str) or not source.strip():
        raise ValueError(f'{field} must name an existing JSON file')
    path = Path(source)
    if not path.is_absolute():
        path = Path(workspace) / path
    path = path.resolve()
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError(f'{field} must be an existing JSON file no larger than 2 MB: {path}')
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f'{field} contains invalid JSON: {path}: {error}') from error
    if not isinstance(value, dict):
        raise ValueError(f'{field} must contain a JSON object: {path}')
    return value, str(path)


# 合并请求文件与内联输入，拒绝字段来源冲突并保留宿主可信上下文。
def resolve_request(args, workspace):
    """File fields never override inline fields or the host's trusted context."""
    request = deepcopy(args)
    sources = {}
    if 'request_path' in request:
        value, sources['request_path'] = _document(request.pop('request_path'), workspace, 'request_path')
        unknown = set(value) - FIELDS - CONTROL_FIELDS
        if unknown:
            raise ValueError('request_path contains unsupported fields: ' + ', '.join(sorted(unknown)))
        inline = set(request) & FIELDS
        if inline:
            raise ValueError('Use request_path or inline plan fields, not both: ' + ', '.join(sorted(inline)))
        for key in CONTROL_FIELDS:
            if key in value and type(value[key]) is not bool:
                raise ValueError(key + ' must be a boolean')
        # 调用级校验开关优先于文件默认值，原计划内容仍只能来自一个来源。
        request = {**value, **request}
    if 'assessment_path' in request:
        if 'assessment' in request:
            raise ValueError('Use assessment_path or assessment, not both')
        request['assessment'], sources['assessment_path'] = _document(
            request.pop('assessment_path'), workspace, 'assessment_path')
    for key in CONTROL_FIELDS:
        if key in request and type(request[key]) is not bool:
            raise ValueError(key + ' must be a boolean')
    return request, sources
