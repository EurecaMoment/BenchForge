"""Strict JSON publication shared by runtime artifacts and exported report helpers."""
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import stat
from uuid import uuid4


@contextmanager
def atomic_text(path):
    """完整写入并落盘后原子发布；会话内文件沿用描述符级受限写入。"""
    destination = Path(path)
    if __package__:
        from service_access.access_scope import current_access
        access = current_access()
        if access is not None:
            from storage.operation_outputs import trusted_output_path
            candidate = destination if destination.is_absolute() else Path(access.root_path) / destination
            if candidate.is_relative_to(Path(access.root_path)) and not trusted_output_path(destination):
                from storage.confined_files import open_write
                with open_write(access.root_path, destination) as binary:
                    text = io.TextIOWrapper(binary, encoding='utf-8', newline='\n')
                    try:
                        yield text
                        text.flush()
                    finally:
                        text.detach()
                return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f'.{destination.name}.{uuid4().hex}.tmp')
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            if destination.exists():
                os.chmod(temporary, stat.S_IMODE(destination.stat().st_mode))
            yield stream
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, value):
    """以统一 UTF-8 和换行格式写入 JSON，拒绝 NaN、Infinity 并保留失败前文件。"""
    with atomic_text(path) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def write_jsonl(path, values):
    """原子发布严格 JSONL；任一记录不合法时不覆盖已有产物。"""
    with atomic_text(path) as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + '\n')
