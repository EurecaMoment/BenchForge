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


def load_service_client_config(path, *, require_operator_token=True, require_scene_policy=True):
    return load_common_config(path, require_operator_token=require_operator_token,
                              require_scene_policy=require_scene_policy)


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

    def __init__(self, report):
        self.preflight = report
        rows = [row for row in report.get('issues', []) if isinstance(row, dict)]
        details = '\n'.join(f"{row.get('path', '/')}: {row.get('message', '')}" for row in rows)
        super().__init__('Scene preflight failed; inspect and repair the complete batch:\n' + details
                         + '\nFull report: ' + str(report.get('report_path', 'not saved')))


def _bind_execution_mode(route, arguments, configured_mode):
    """Make the server's configured execution mode explicit on each request."""
    if route not in MODE_ROUTES:
        return arguments

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


def load_workbench_config(path):
    return load_service_client_config(path, require_operator_token=False, require_scene_policy=False)


def service_read(config, route, value):
    return post_service_json(config['service_url'], route, value, token=config['operator_token'], timeout=3)


def session_runs(config, session, sessions=()):
    value = service_read(config, '/workbench/session-runs', {
        'session_id': session, 'related_session_ids': list(dict.fromkeys(sessions))[:8],
    })
    return value['runs'] if value else []


def production_observation(config, run_id):
    return service_read(config, '/observe', {'run_id': run_id})
