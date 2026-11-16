"""Keep documented source layering and module dependencies acyclic."""

import ast
from pathlib import Path
import unittest


class SourceBoundaryTests(unittest.TestCase):
    def test_modules_follow_layering_without_import_cycles(self):
        root = Path(__file__).resolve().parents[1] / "src"
        modules = {}
        for base in ("capabilities", "storage", "service_access", "pipelines", "server"):
            for file in (root / base).rglob("*.py"):
                name = ".".join(file.relative_to(root).with_suffix("").parts)
                modules[name.removesuffix(".__init__")] = file
        graph = {name: set() for name in modules}
        for name, file in modules.items():
            for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    targets = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    base = node.module or ""
                    if node.level:
                        parent = (
                            name.split(".") if file.name == "__init__.py" else name.split(".")[:-1]
                        )
                        base = ".".join(
                            parent[: len(parent) - node.level + 1] + ([base] if base else [])
                        )
                    targets = [base, *(base + "." + alias.name for alias in node.names)]
                else:
                    continue
                graph[name].update(
                    target for target in targets if target in modules and target != name
                )
                if name.startswith("storage."):
                    self.assertFalse(any(target.startswith("capabilities.") for target in targets),
                                     f"Business rule imported into persistence: {name}")
                if name.startswith("capabilities.task_catalog."):
                    self.assertFalse(any(target.startswith(("capabilities.dataset_authoring.",
                                                            "capabilities.evaluation.")) for target in targets),
                                     f"Catalog imported an execution implementation: {name}")
                if name.startswith("capabilities.dataset_authoring."):
                    self.assertFalse(any(target.startswith("capabilities.evaluation.") for target in targets),
                                     f"Dataset authoring imported its evaluator: {name}")
                if name.startswith("capabilities.scene_authoring."):
                    self.assertFalse(any(target.startswith("capabilities.isaac_sim.") for target in targets),
                                     f"Scene contract imported a simulator implementation: {name}")
                if name.split(".")[0] in {"capabilities", "storage", "service_access"}:
                    self.assertFalse(
                        any(
                            target.split(".")[0] in {"pipelines", "server", "dsh", "ui"}
                            for target in targets
                        ),
                        f"Entry-layer dependency in {name}: {targets}",
                    )
        visited, active = set(), []

        def visit(name):
            self.assertNotIn(name, active, "Import cycle: " + " -> ".join([*active, name]))
            if name in visited:
                return
            active.append(name)
            for target in graph[name]:
                visit(target)
            active.pop()
            visited.add(name)

        for name in graph:
            visit(name)
