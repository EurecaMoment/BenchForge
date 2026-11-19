"""Read dataset inputs through the conversation's authorized file boundary."""

from contextlib import contextmanager
import io
import json
from pathlib import Path

from service_access.access_scope import current_access, workspace_path
from storage.confined_files import open_read


# 相对指定基目录解析输入路径；存在访问范围时强制按会话工作区授权。
def source_path(value, *, base=None):
    path = Path(value).expanduser()
    if base is not None and not path.is_absolute():
        path = Path(base) / path
    return workspace_path(path) if current_access() is not None else path.resolve()


# 以受限文件句柄读取当前会话输入，无访问范围时读取普通文件。
@contextmanager
def open_source(value, *, base=None):
    path = source_path(value, base=base)
    access = current_access()
    if access is not None:
        with open_read(access.root_path, path) as stream:
            yield stream
    else:
        with path.open('rb') as stream:
            yield stream


# 把已授权的二进制源包装为支持UTF-8签名的文本流。
@contextmanager
def text_source(value):
    with open_source(value) as stream:
        text = io.TextIOWrapper(stream, encoding='utf-8-sig', newline='')
        try:
            yield text
        finally:
            text.detach()


# 从已授权文本源读取JSON对象。
def read_source(value):
    with text_source(value) as stream:
        return json.load(stream)


# 从已授权文本源读取非空JSONL记录。
def source_rows(value):
    with text_source(value) as stream:
        return [json.loads(line) for line in stream if line.strip()]


# 通过实际打开文件验证输入可读性，并返回解析后的路径。
def require_source(value, *, base=None):
    path = source_path(value, base=base)
    with open_source(path):
        pass
    return path
