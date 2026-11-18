"""Read dataset inputs through the conversation's authorized file boundary."""

from contextlib import contextmanager
import io
import json
from pathlib import Path

from service_access.access_scope import current_access, workspace_path
from storage.confined_files import open_read


def source_path(value, *, base=None):
    path = Path(value).expanduser()
    if base is not None and not path.is_absolute():
        path = Path(base) / path
    return workspace_path(path) if current_access() is not None else path.resolve()


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


@contextmanager
def text_source(value):
    with open_source(value) as stream:
        text = io.TextIOWrapper(stream, encoding='utf-8-sig', newline='')
        try:
            yield text
        finally:
            text.detach()


def read_source(value):
    with text_source(value) as stream:
        return json.load(stream)


def source_rows(value):
    with text_source(value) as stream:
        return [json.loads(line) for line in stream if line.strip()]


def require_source(value, *, base=None):
    path = source_path(value, base=base)
    with open_source(path):
        pass
    return path
