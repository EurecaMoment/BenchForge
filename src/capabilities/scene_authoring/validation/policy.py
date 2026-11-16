"""Execution mode and operator policy resolution for scene requests."""
import json
from pathlib import Path
from service_access.runtime_config import environment_value
from capabilities.scene_authoring.contracts.scene_design import references_excluded_source


EXECUTION_MODES = {"production", "exploration"}


def resolve_execution_mode(value=None):
    configured = environment_value("BENCHFORGE_EXECUTION_MODE")
    if value is not None and configured is not None and value != configured:
        raise ValueError(
            f"request execution_mode {value!r} conflicts with configured mode {configured!r}"
        )
    if value is None:
        value = configured
    if value is None:
        return "exploration"
    if value not in EXECUTION_MODES:
        raise ValueError("execution_mode must be production or exploration")
    return value


def policy_for(root, execution_mode=None):
    mode = resolve_execution_mode(execution_mode)
    explicit = environment_value("BENCHFORGE_SCENE_POLICY")
    path = Path(explicit) if explicit else Path(root) / "scene_policy.json"
    value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    enabled = value.get("enabled") is True and value.get("enforce_realism") is True
    if mode == "production" and not enabled:
        raise ValueError(
            "production execution requires an enabled scene policy with enforce_realism=true"
        )
    if mode == "exploration":
        return {}
    if enabled:
        value["require_scene_brief"] = True
        return value
    return {}


def check_references(value, policy):
    if references_excluded_source(value, policy):
        raise ValueError(
            "Scene was removed by the user; its source and derived artifacts cannot be reused"
        )
