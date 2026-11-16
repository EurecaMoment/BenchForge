"""Reconcile every delivered shard with the immutable production plan."""

from collections import Counter
from pathlib import Path
from capabilities.task_catalog.allocation import counts_for
from storage.artifacts import read, rows


def verify_delivery(plan, outputs, stage):
    """Check saved public items, not a producer's self-reported requested count."""
    expected = counts_for(plan["inputs"]["selections"])
    uses = "templates.produce" if stage == "S4" else "data.package"
    counts, datasets = Counter(), set()
    for output in outputs:
        if output["uses"] != uses:
            continue
        result = output["result"]
        dataset = result.get("dataset")
        if not dataset and output.get("result_path"):
            dataset = read(Path(output["result_path"]).with_name("request.json")).get("dataset")
        if not dataset:
            raise ValueError("Delivery requires the original dataset reference")
        root = Path(dataset).resolve()
        if root in datasets:
            raise ValueError("A dataset cannot count twice toward planned delivery")
        datasets.add(root)
        questions = rows(root / "questions.jsonl")
        counts.update(row["template_id"] for row in questions)
    if counts != expected:
        raise ValueError("Delivered per-template quantities do not match the active S1 plan")
    return dict(counts)
