"""Small, explicit artifact lineage for composable benchmark pipelines.

The graph records what a pipeline step produced and why it is releasable. It
uses human-readable versions and manifests; it deliberately does not hash
large inputs or scan unrelated files.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Any


STATUSES = {"draft", "candidate", "verified", "rejected", "superseded"}


# 返回用于产物记录的 UTC ISO 时间戳。
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# 递归遍历字典和列表，产出所有带键的后代值。
def _walk(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


# 提取参数中声明的来源路径并去重，不读取或推断文件内容。
def evidence_refs(arguments: dict[str, Any]) -> list[str]:
    """Return declared source paths without inspecting their contents."""
    keys = {
        "evidence", "input", "source", "dataset", "bundle", "items",
        "predictions", "scene_program_path", "scene_file", "asset_path",
    }
    result = []
    for key, value in _walk(arguments):
        if key not in keys:
            continue
        values = value if isinstance(value, list) else [value]
        for item in values:
            if isinstance(item, str) and item not in result:
                result.append(item)
    return result


# 递归提取延迟绑定引用的根步骤名称，保持首次出现顺序。
def binding_roots(value: Any) -> list[str]:
    roots = []
    if isinstance(value, str) and value.startswith("$"):
        root = value[1:].split(".", 1)[0]
        if root and root not in roots:
            roots.append(root)
    elif isinstance(value, dict):
        for child in value.values():
            for root in binding_roots(child):
                if root not in roots:
                    roots.append(root)
    elif isinstance(value, list):
        for child in value:
            for root in binding_roots(child):
                if root not in roots:
                    roots.append(root)
    return roots


# 优先采用显式产物类型，否则按操作语义推断已知类型。
def artifact_type(operation: str, declared: str | None = None) -> str:
    if declared:
        return declared
    if operation in ("templates.produce",):
        return "benchmark.dataset"
    if operation in ("data.package",):
        return "benchmark.package"
    if operation == "pipeline":
        return "pipeline.output"
    if operation.startswith("benchforge."):
        return "scene." + operation.removeprefix("benchforge.")
    return "pipeline.output"


# 构造统一的契约检查记录，标注通过状态与说明。
def _check(name: str, passed: bool, message: str) -> dict[str, Any]:
    return {"id": name, "status": "passed" if passed else "failed", "message": message}


# 只验证仓库拥有的产物结构契约；未知类型明确标为不适用，不当作真实性证明。
def contract_result(kind: str, result: Any) -> dict[str, Any]:
    """Apply only shape-level contracts owned by this repository.

    Content and simulator truth remain task-specific. Unknown artifact types
    are recorded as not_applicable instead of being silently treated as proof.
    """
    checks: list[dict[str, Any]] = []
    if not isinstance(result, dict):
        checks.append(_check("result_object", False, "step result must be a JSON object"))
    elif kind == "benchmark.dataset":
        for name in ("dataset", "report", "accepted"):
            checks.append(_check(name, name in result, f"dataset result contains {name}"))
    elif kind == "benchmark.package":
        for name in ("dataset", "accepted", "quality_report"):
            checks.append(_check(name, name in result, f"package result contains {name}"))
        checks.append(_check("bundle", any(key.endswith("_bundle") for key in result),
                             "package result exposes a public or authority bundle"))
    elif kind == "pipeline.output":
        checks.append(_check("result_object", isinstance(result, dict), "pipeline output is structured"))
    else:
        return {"status": "not_applicable", "checks": [], "errors": [],
                "note": "No generic content contract; use the operation-specific verifier."}
    errors = [item["message"] for item in checks if item["status"] == "failed"]
    return {"status": "failed" if errors else "passed", "checks": checks,
            "errors": errors, "note": "Shape contract only; it does not establish GT or scene quality."}


@dataclass
class ArtifactRecord:
    artifact_id: str
    artifact_type: str
    version: int
    parents: list[str] = field(default_factory=list)
    producer: str = ""
    evidence_refs: list[str] = field(default_factory=list)
    contract_results: dict[str, Any] = field(default_factory=dict)
    status: str = "candidate"
    result_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)

    # 校验产物状态属于允许集合，拒绝无效账本记录。
    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError(f"Unknown artifact status: {self.status}")

    # 将产物记录转换为带版本标识的可持久化字典。
    def to_dict(self) -> dict[str, Any]:
        return {"schema": "benchforge.artifact/v1", **asdict(self)}


class ArtifactGraph:
    """Append-only lineage manifest for one persisted pipeline run."""

    # 绑定运行目录及产物、修复日志，并加载已有产物记录。
    def __init__(self, run_root: str | Path):
        self.root = Path(run_root).resolve()
        self.path = self.root / "artifacts.jsonl"
        self.repair_path = self.root / "repair-plans.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        self.records: list[dict[str, Any]] = []
        if self.path.exists():
            with self.path.open(encoding="utf-8") as stream:
                self.records = [json.loads(line) for line in stream if line.strip()]

    # 取得同一步骤下一产物版本号，使重复执行保留历史。
    def next_version(self, step_id: str) -> int:
        versions = [row.get("version", 0) for row in self.records
                    if row.get("metadata", {}).get("step_id") == step_id]
        return max(versions, default=0) + 1

    # 将产物记录追加到 JSONL 账本并同步内存索引。
    def append(self, record: ArtifactRecord) -> dict[str, Any]:
        payload = record.to_dict()
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self.records.append(payload)
        return payload

    # 把修复方案追加到独立日志，保留此前修复决策。
    def append_repair(self, plan: dict[str, Any]) -> dict[str, Any]:
        with self.repair_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(plan, ensure_ascii=False) + "\n")
        return plan

    # 按产物身份筛选账本；未指定身份时返回全部记录副本。
    def find(self, artifact_id: str | None = None) -> list[dict[str, Any]]:
        if artifact_id is None:
            return list(self.records)
        return [row for row in self.records if row.get("artifact_id") == artifact_id]

    # 汇总产物数量与各状态计数，并提供账本路径。
    def summary(self) -> dict[str, Any]:
        counts = {status: 0 for status in sorted(STATUSES)}
        for row in self.records:
            status = row.get("status")
            if status in counts:
                counts[status] += 1
        return {"total": len(self.records), "by_status": counts,
                "path": str(self.path)}


# 根据结构契约决定候选或拒绝状态，操作完成本身不代表质量已验证。
def record_status(result: Any, contract: dict[str, Any]) -> str:
    if contract.get("status") == "failed":
        return "rejected"
    # A completed operation is still only a candidate until its
    # operation-specific quality gate explicitly marks it verified.
    return "candidate"


# 结合操作、父产物、结果及错误构造带版本的产物记录并追加到账本。
def make_record(*, graph: ArtifactGraph, run_id: str, step_id: str,
                operation: str, arguments: dict[str, Any], result: Any,
                parent_ids: list[str], declared_type: str | None = None,
                result_path: str | None = None,
                status: str | None = None,
                error: str | None = None) -> dict[str, Any]:
    kind = artifact_type(operation, declared_type)
    contract = contract_result(kind, result)
    if error:
        contract = {"status": "failed", "checks": [], "errors": [error],
                    "note": "Step execution failed; result is not release evidence."}
    version = graph.next_version(step_id)
    record = ArtifactRecord(
        artifact_id=f"artifact:{run_id}:{step_id}:v{version}",
        artifact_type=kind,
        version=version,
        parents=list(dict.fromkeys(parent_ids)),
        producer=operation,
        evidence_refs=evidence_refs(arguments),
        contract_results=contract,
        status=status or ("rejected" if error else record_status(result, contract)),
        result_path=result_path,
        metadata={"run_id": run_id, "step_id": step_id,
                  "operation": operation, "error": error},
    )
    return graph.append(record)


# 按运行、产物身份及状态读取产物图，返回记录与摘要。
def inspect(args, directory=None, config=None):
    graph = ArtifactGraph(args["run"])
    rows = graph.find(args.get("artifact_id"))
    if args.get("status"):
        rows = [row for row in rows if row.get("status") == args["status"]]
    return {"run": str(graph.root), "artifacts": rows, "summary": graph.summary()}
