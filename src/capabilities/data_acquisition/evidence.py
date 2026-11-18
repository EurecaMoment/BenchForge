"""Import task evidence and resolve its source references."""
from collections import Counter

from storage.artifacts import select
from storage.artifacts import write_jsonl as jsonl
from capabilities.data_acquisition.source_files import (
    read_source, require_source, source_path, source_rows,
)


def import_evidence(args, directory):
    source = source_path(args["input"])
    accepted = []
    ids = set()
    for record in source_rows(source):
        if record["id"] in ids:
            raise ValueError(f"Duplicate evidence id: {record['id']}")
        ids.add(record["id"])
        origin = record["provenance"]
        if origin["kind"] not in ("official", "simulation", "program", "human", "prediction"):
            raise ValueError("Unknown provenance kind")
        reference = require_source(origin["path"], base=source.parent)
        record["provenance"] = {**origin, "path": str(reference)}
        if origin["kind"] != "prediction":
            # Recompute fields from the source, rather than trusting supplied answers.
            if reference.suffix.lower() != '.json':
                raise ValueError('provenance.path must reference a JSON source record. For raw depth/images, compute a reproducible JSON oracle in task code and list binary inputs under private assets.')
            raw = read_source(reference)
            facts = {}
            for name, pointer in record["selectors"].items():
                facts[name] = select(raw, pointer)
            record["facts"] = facts
        for field in ('media', 'assets'):
            record[field] = [str(require_source(value, base=source.parent))
                             for value in record.get(field, [])]
        accepted.append(record)
    jsonl(directory / "evidence.jsonl", accepted)
    counts = Counter(r["provenance"]["kind"] for r in accepted)
    return {"evidence": str(directory / "evidence.jsonl"), "count": len(accepted), "provenance_counts": dict(counts)}
