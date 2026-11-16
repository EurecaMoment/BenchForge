"""Shared deterministic allocation of planned questions by template."""

from collections import Counter


def counts_for(selections):
    counts = Counter()
    for selection in selections:
        ids, count = selection["template_ids"], selection["count"]
        if not ids or type(count) is not int or count < len(ids):
            raise ValueError(
                "Each selection count must cover its explicitly selected templates at least once"
            )
        for i, tid in enumerate(ids):
            counts[tid] += count // len(ids) + (i < count % len(ids))
    return counts
