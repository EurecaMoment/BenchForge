"""Bind concrete S4 shards and S5 packages to the saved plan allocation."""

from collections import Counter
from capabilities.task_catalog.allocation import counts_for


# 解析每个生产步骤的模板配额，要求总分配与计划选择完全一致。
def step_allocations(steps, selections):
    expected = counts_for(selections)
    if not expected:
        raise ValueError("Production requires an explicit plan allocation")
    allocated, result = Counter(), {}
    for step in steps:
        if step["uses"] != "templates.produce":
            continue
        selection = step.get("arguments", {}).get("selections")
        if selection == "$inputs.selections":
            selection = selections
        if not isinstance(selection, list):
            raise ValueError(
                "S4 selections must reference plan inputs or declare a concrete shard allocation"
            )
        result[step["id"]] = dict(counts_for(selection))
        allocated.update(result[step["id"]])
    if allocated != expected:
        raise ValueError("S4 shard allocations must sum to the exact per-template S1 plan")
    producers = set(result)
    packaged = [
        step.get("arguments", {}).get("dataset") for step in steps if step["uses"] == "data.package"
    ]
    if Counter(packaged) != Counter("$" + key + ".dataset" for key in producers):
        raise ValueError("S5 must package every planned S4 dataset exactly once")
    return result
