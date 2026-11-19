"""Atomic JSON writes and confined paths for BenchForge artifacts."""

from pathlib import Path
from pathlib import PurePosixPath
from storage.json_io import write_json as write_json


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
