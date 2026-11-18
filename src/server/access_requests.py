"""Authorize model-facing service requests before domain handlers read files."""

from sqlalchemy import select
from database.schema import runs
from service_access.access_scope import current_access, workspace_path

_START = {"/start", "/capture", "/layout", "/asset", "/diffusion", "/sam3",
          "/trellis", "/depth", "/mesh-import", "/extend", "/refine"}
_PATHS = {"source_image", "source_mask", "source_mesh", "source_gaussian", "scene_program_path"}


def require_run(engine, run_id):
    access = current_access()
    if access is None:
        return  # Trusted operator/maintenance call, separately authenticated.
    with engine.connect() as connection:
        row = connection.execute(select(runs).where(runs.c.id == run_id)).mappings().first()
    if not row:
        raise PermissionError("Run is unavailable in this conversation")
    context = row["plan"].get("context") or {}
    workspace = row.get("workspace_id") or context.get("workspace_id")
    session = row.get("session_id") or context.get("session_id")
    if workspace not in access.scope.ledger_namespaces or session != access.scope.session_id:
        raise PermissionError("Run is unavailable in this conversation")


def authorize_request(store, route, value):
    access = current_access()
    if access is None:
        if route == "/task-code":
            raise PermissionError("Task code requires a verified conversation identity")
        return value
    if not isinstance(value, dict):
        raise ValueError("Request must be an object")
    for key in ("run_id", "task_id", "parent_task_id"):
        if value.get(key):
            require_run(store.repo.engine, str(value[key]).split(".scene", 1)[0])
    if route.startswith("/session-search/"):
        raise PermissionError("Session indexing is a Host-only operation")
    result = dict(value)
    if route == '/stage-review/read':
        if value.get('workspace') not in (None, access.root_path) or value.get('session') not in (None, access.scope.session_id):
            raise PermissionError('Review query belongs to another conversation')
        return {**result, 'workspace': access.root_path, 'session': access.scope.session_id}
    if route == '/workbench/session-runs':
        if value.get('session_id') not in (None, access.scope.session_id) or value.get('related_session_ids'):
            raise PermissionError('Cross-conversation queries require an explicit Host grant')
        return {'session_id': access.scope.session_id}
    if route == '/workbench/scene-catalog':
        raise PermissionError('Use the authorized shared asset catalog')
    if route in _START:
        result["context"] = {"workspace_id": access.scope.workspace_id, "session_id": access.scope.session_id}
        if result.get("request_key"):
            # Idempotency keys cannot collide with another session's request.
            result["request_key"] = f"dsh:{access.scope.session_id}:" + str(result["request_key"])

    def paths(item):
        if isinstance(item, dict):
            for key, child in item.items():
                if key in _PATHS and isinstance(child, str):
                    if key != 'scene_program_path':
                        from capabilities.asset_generation.generation import GenerationTools
                        GenerationTools(store.root, engine=store.repo.engine).source_path(child)
                        continue
                    # Scene submissions are task-authored JSON. Production
                    # evidence has separate export APIs and is not an alternate
                    # workspace for this input contract.
                    workspace_path(child)
                else:
                    paths(child)
        elif isinstance(item, list):
            for child in item:
                paths(child)
    paths(result)
    return result
