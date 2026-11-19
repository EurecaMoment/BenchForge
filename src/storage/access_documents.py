"""Authoritative pipeline documents; filesystem copies are disposable projections."""

import hashlib
import json
from pathlib import Path, PurePosixPath

from sqlalchemy import func, select
from database.access import identifier, read_transaction, validate_scope
from database.schema import pipeline_documents
from database.transactions import lock_entity


class DocumentConflict(RuntimeError):
    """Another writer changed this document; reload before applying an update."""


def run_document_key(root):
    """用发布后的绝对运行路径生成文档键，执行器与只读查询共享同一身份规则。"""
    from storage.operation_outputs import logical_path
    return hashlib.sha256(str(logical_path(Path(root).resolve())).encode()).hexdigest()


# 校验运行编号与相对文档名，并补齐当前工作区和会话的复合身份。
def _identity(scope, run_id, name):
    identifier(run_id, "pipeline run")
    if (not isinstance(name, str) or not name or len(name) > 240 or "\\" in name or ":" in name
            or PurePosixPath(name).is_absolute() or any(part in ("", ".", "..") for part in name.split("/"))):
        raise ValueError("Invalid pipeline document name")
    return dict(workspace_id=scope.workspace_id, session_id=scope.session_id, run_id=run_id, name=name)


# 按完整文档身份构造精确查询，避免不同运行或会话的同名文档混用。
def _query(identity):
    return select(pipeline_documents).where(*(pipeline_documents.c[key] == value for key, value in identity.items()))


# 只对外返回文档版本、正文及摘要，隐藏数据库内部身份字段。
def _public(row):
    return {key: row[key] for key in ("revision", "document", "sha256")} if row else None


# 将对象规范编码为有大小上限且禁止非有限数的 JSON，并计算内容摘要。
def encode_document(document):
    if not isinstance(document, dict):
        raise ValueError("Pipeline document must be an object")
    raw = json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Pipeline document exceeds 8 MiB; store large products separately")
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


# 在访问作用域内读取流水线文档；外部查询服务须启用独立读角色检查。
def read_document(engine, scope, run_id, name, *, enforce_role=False):
    """Trusted pipeline read; external query services must enforce their reader role."""
    identity = _identity(scope, run_id, name)
    with read_transaction(engine, scope, enforce_role=enforce_role) as connection:
        return _public(connection.execute(_query(identity)).mappings().first())


# 以预期版本执行比较后写入；版本零只允许新建，冲突时拒绝覆盖。
def write_document(engine, scope, run_id, name, document, *, expected_revision):
    """Compare-and-swap a service-authored document; zero creates, never overwrites."""
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError("expected_revision must be a non-negative integer")
    identity = _identity(scope, run_id, name)
    value, digest = encode_document(document)
    with engine.begin() as connection:
        validate_scope(connection, scope)
        lock_entity(connection, "pipeline-document", *identity.values())
        row = connection.execute(_query(identity).with_for_update()).mappings().first()
        current = row["revision"] if row else 0
        if current != expected_revision:
            raise DocumentConflict("Pipeline document changed; reload the committed revision")
        revision = current + 1
        fields = dict(document=value, sha256=digest, revision=revision)
        if row:
            connection.execute(pipeline_documents.update().where(
                *(pipeline_documents.c[key] == val for key, val in identity.items())).values(**fields, updated_at=func.now()))
        else:
            connection.execute(pipeline_documents.insert().values(**identity, **fields))
        return {"revision": revision, "document": value, "sha256": digest}
