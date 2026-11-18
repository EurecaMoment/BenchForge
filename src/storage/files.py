"""Atomic JSON writes and confined paths for BenchForge artifacts."""

import json
import os
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4


def safe_path(root: Path, relative: str, must_exist: bool = False) -> Path:
    root = root.resolve()
    rel = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or ":" in relative
        or rel.is_absolute()
        or any(part in {".", ".."} for part in relative.split("/"))
    ):
        raise ValueError(f"Invalid relative artifact path: {relative}")
    path = root
    for part in rel.parts:
        path /= part
        if path.is_symlink():
            raise ValueError(f"Symlinks are forbidden: {relative}")
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"Artifact path escapes its root: {relative}")
    if must_exist and not path.is_file():
        raise FileNotFoundError(relative)
    return path


def write_json(path: Path, value: Any) -> None:
    from service_access.access_scope import current_access
    access = current_access()
    if access is not None:
        from storage.operation_outputs import trusted_output_path
        candidate = path if path.is_absolute() else Path(access.root_path) / path
        if candidate.is_relative_to(Path(access.root_path)) and not trusted_output_path(path):
            from storage.confined_files import open_write
            with open_write(access.root_path, path) as output:
                output.write((json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode())
            return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp_{uuid4().hex}")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
