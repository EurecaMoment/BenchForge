"""Read the client configuration and call the scene production service."""
import json
from io import BytesIO
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from service_access.service_config import (
    environment_value as environment_value,
    load_common_config,
    resolve_service_config_file as resolve_service_config_file,
)


# 复用服务端的公共解析规则，客户端不初始化数据库或模型运行设置。
def load_service_client_config(path, *, require_operator_token=True, require_scene_policy=True):
    return load_common_config(path, require_operator_token=require_operator_token,
                              require_scene_policy=require_scene_policy)


# 发送带令牌和可选宿主作用域票据的 JSON 请求，保留 HTTP 错误供调用方处理。
def post_service_json(base_url, route, arguments, *, token, timeout, use_proxy=False):
    """Send a JSON request and leave HTTP errors available to the caller."""
    from service_access.access_scope import current_access, sign_ticket
    access = current_access()
    request = Request(
        base_url.rstrip('/') + route,
        data=json.dumps(arguments).encode('utf-8'),
        method='POST',
        headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + token,
            **({'X-BenchForge-Scope': sign_ticket(access, token)} if access else {}),
        },
    )
    open_url = urlopen if use_proxy else build_opener(ProxyHandler({})).open
    with open_url(request, timeout=timeout) as response:
        return json.load(response)


MODE_ROUTES = {'/start', '/capture', '/refine', '/extend', '/retry', '/dataset', '/scene-preflight'}


class ScenePreflightRejected(ValueError):
    """A complete service diagnostic without a domain implementation dependency."""
    code = 'scene_preflight_failed'

    # 保存完整预检报告，并将所有问题位置及报告路径合成为可修复的错误提示。
    def __init__(self, report):
        self.preflight = report
        rows = [row for row in report.get('issues', []) if isinstance(row, dict)]
        details = '\n'.join(f"{row.get('path', '/')}: {row.get('message', '')}" for row in rows)
        super().__init__('Scene preflight failed; inspect and repair the complete batch:\n' + details
                         + '\nFull report: ' + str(report.get('report_path', 'not saved')))


# 为相关业务路由显式绑定服务执行模式，拒绝请求尝试覆盖配置模式。
def _bind_execution_mode(route, arguments, configured_mode):
    """Make the server's configured execution mode explicit on each request."""
    if route not in MODE_ROUTES:
        return arguments

    # 为单个请求对象补齐执行模式，并拒绝与服务配置冲突的值。
    def bind(value):
        supplied = value.get('execution_mode')
        if supplied is not None and supplied != configured_mode:
            raise ValueError(
                f'execution_mode {supplied} conflicts with configured {configured_mode} mode'
            )
        return {**value, 'execution_mode': configured_mode}

    if route == '/start':
        intents = arguments.get('intents')
        if not isinstance(intents, list):
            return bind(arguments)
        return {**arguments, 'intents': [bind(intent) for intent in intents]}

    request = bind(arguments)
    if isinstance(request.get('intent'), dict):
        request['intent'] = bind(request['intent'])
    return request


# 按配置调用业务服务，将结构化预检拒绝响应恢复为携带报告的专用异常。
def service_call(route, arguments, config_path):
    config = load_service_client_config(config_path)
    arguments = _bind_execution_mode(route, arguments, config['execution_mode'])
    try:
        return post_service_json(
            config['service_url'], route, arguments, token=config['operator_token'], timeout=60,
        )
    except HTTPError as error:
        body = error.read()
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            payload = None
        if (error.code == 400 and isinstance(payload, dict) and
                payload.get('code') == ScenePreflightRejected.code and
                isinstance(payload.get('preflight'), dict)):
            raise ScenePreflightRejected(payload['preflight']) from error
        raise HTTPError(error.url, error.code, error.reason, error.headers, BytesIO(body)) from error


# 加载工作台读取配置，允许缺少操作者令牌和场景执行策略。
def load_workbench_config(path):
    return load_service_client_config(path, require_operator_token=False, require_scene_policy=False)


# 以短超时向服务发送工作台只读查询。
def service_read(config, route, value):
    return post_service_json(config['service_url'], route, value, token=config['operator_token'], timeout=3)


# 读取主会话及去重后的有限关联会话运行列表，空响应返回空列表。
def session_runs(config, session, sessions=()):
    value = service_read(config, '/workbench/session-runs', {
        'session_id': session, 'related_session_ids': list(dict.fromkeys(sessions))[:8],
    })
    return value['runs'] if value else []


# 按运行编号读取生产过程的当前观测快照。
def production_observation(config, run_id):
    return service_read(config, '/observe', {'run_id': run_id})
