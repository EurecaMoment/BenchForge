"""Compose domain artifact validators without coupling the pipelines to each other."""

from hashlib import sha256
from pathlib import Path
from pipelines.benchmark.execution.review_checks import check_artifact as benchmark_artifact
from pipelines.scene_production.review_checks import check_repair_artifact


# 分派普通生产或场景修复产物检查，并确认自检期间文件字节没有变化。
def check_artifact(workspace, session, row):
    if not row["key"].startswith("scene-repair:"):
        return benchmark_artifact(workspace, session, row)
    root = Path(workspace).resolve()
    file = (root / row["artifact"]).resolve()
    if not file.is_relative_to(root) or not file.is_file():
        raise ValueError("Repair review requires an existing artifact inside this workspace")
    before = file.read_bytes()
    checks = check_repair_artifact(file, row["stage"])
    if before != file.read_bytes():
        raise ValueError("Repair artifact changed during its self-check")
    return {"artifact": str(file), "digest": sha256(before).hexdigest(), "checks": checks}
