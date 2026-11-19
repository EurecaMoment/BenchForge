"""Execution mode and operator policy resolution for scene requests."""
import json
import re
from pathlib import Path
from service_access.runtime_config import environment_value
from capabilities.scene_authoring.contracts.scene_design import references_excluded_source


EXECUTION_MODES = {"production", "exploration"}
STRUCTURE = re.compile(r'^(?:floor|ceiling|wall(?:_|$)|ground|terrain|pavement|asphalt|roof|road(?:_|$)|bay_line|rug|carpet|window|door(?:_|$))', re.I)


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
    # A name cannot turn a chair-sized cube into a floor or wall.
    size = obj.get('size', [])
    return (bool(STRUCTURE.search(str(obj.get('id', '')))) and len(size) == 3
            and obj.get('dynamic') is False and min(size) <= .4
            and max(size) >= 1)


# 验证显式静态固定关系的目标、轴与相对面，拒绝非法自连接或动态固定。
def attachment(obj, by_id):
    value = obj.get('metadata', {}).get('spatial_attachment')
    if value is None:return None
    if (not isinstance(value, dict) or obj.get('dynamic') is not False or
            value.get('target') not in by_id or value['target'] == obj['id'] or
            by_id[value['target']].get('dynamic') is not False or value.get('method') != 'static_mount' or
            type(value.get('axis')) is not int or value['axis'] not in (0, 1, 2) or
            value.get('object_face') not in ('min', 'max') or value.get('target_face') not in ('min', 'max') or
            value['object_face'] == value['target_face']):
        raise ValueError(obj['id'] + ': invalid explicit static mounting relation')
    return value


# 检查对象是否具有细分构造，区分真实建模结构与单一外包络占位体。
def detailed_construction(obj):
    """A modeled table/fixture is distinct from a single envelope placeholder.

    This admits geometry for capture, never certifies its visual identity.
    """
    parts = obj.get('parts', [])
    return (obj.get('kind') == 'composite' and len(parts) >= 3 and
            len({tuple(part.get('offset', [0, 0, 0])) for part in parts}) >= 3)
