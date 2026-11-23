"""Execution mode and operator policy resolution for scene requests."""
import json
from pathlib import Path
from service_access.runtime_config import environment_value
from capabilities.scene_authoring.contracts.scene_design import references_excluded_source
from capabilities.scene_authoring.validation.asset_policy import is_structural_geometry


EXECUTION_MODES = {"production", "exploration"}


# 合并请求与环境的执行模式，拒绝冲突并验证生产或探索模式。
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


# 读取场景策略，生产模式要求启用真实性约束并强制保存场景简报。
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


# 拒绝继续引用用户已排除的场景来源及其派生产物。
def check_references(value, policy):
    if references_excluded_source(value, policy):
        raise ValueError(
            "Scene was removed by the user; its source and derived artifacts cannot be reused"
        )


# 结合静态标记和尺寸判断是否为墙地等薄型结构，避免仅凭名称认可大型占位块。
def structure(obj):
    return is_structural_geometry(obj)


# 检查对象是否具有细分构造，区分真实建模结构与单一外包络占位体。
def detailed_construction(obj):
    """A modeled table/fixture is distinct from a single envelope placeholder.

    This admits geometry for capture, never certifies its visual identity.
    """
    parts = obj.get('parts', [])
    return (obj.get('kind') == 'composite' and len(parts) >= 3 and
            len({tuple(part.get('offset', [0, 0, 0])) for part in parts}) >= 3)
