"""Session ownership for remote Isaac Sim jobs in the shared database."""

import json
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from database.config import resolve_workspace_url
from database.engine import engine_for
from database.migrations import initialize_schema
from database.schema import native_jobs
from database.transactions import insert_missing, transaction
from storage.operation_index import query_operations
from storage.access_identity import workspace_namespace


class NativeJobs:
    # 绑定工作区、原生任务回执位置及共享数据库命名空间。
    def __init__(self, workspace):
        self.root = Path(workspace).resolve()
        self.file = self.root / ".benchforge" / "isaac_sim" / "receipts"
        self.engine = engine_for(resolve_workspace_url(self.root))
        initialize_schema(self.engine)
        self.workspace_id = workspace_namespace(self.engine, self.root)

    # 幂等登记远端任务与会话、请求的绑定，拒绝与已有归属不一致的回执。
    def remember(self, endpoint, job, session, request_id=None):
        job_id = getattr(job, "job_id", None) or getattr(job, "id")
        identity = request_id if request_id is not None else getattr(job, "request_id", "")
        values = {"workspace_id": self.workspace_id, "endpoint": endpoint, "job_id": job_id,
                  "session_id": session, "request_id": identity}
        with transaction(self.engine) as connection:
            insert_missing(connection, native_jobs, values, ["workspace_id", "endpoint", "job_id"])
            row = connection.execute(select(native_jobs.c.session_id, native_jobs.c.request_id).where(
                native_jobs.c.workspace_id == self.workspace_id, native_jobs.c.endpoint == endpoint,
                native_jobs.c.job_id == job_id)).first()
            if row != (session, identity):
                raise PermissionError("Native job is already associated with another session or request")

    # 确认远端任务属于指定会话，必要时仅从可验证的历史回执恢复归属。
    def require(self, endpoint, job_id, session):
        with self.engine.connect() as connection:
            row = connection.execute(select(native_jobs.c.session_id, native_jobs.c.request_id).where(
                native_jobs.c.workspace_id == self.workspace_id, native_jobs.c.endpoint == endpoint,
                native_jobs.c.job_id == job_id)).first()
        if not row:
            row = self._recover_receipt(endpoint, job_id, session)
        if not row or row[0] != session:
            raise PermissionError("Native job is not registered to this session and simulator endpoint")
        return row[1]

    # 核对不可变请求身份后恢复旧任务归属，避免仅凭任务编号接管资源。
    def _recover_receipt(self, endpoint, job_id, session):
        """Recover an old ownership receipt only after checking its immutable request."""
        expected_session = "" if session == "local-cli" else session
        folders = query_operations(self.root, sessions=[expected_session] if expected_session else [],
                                   history=not bool(expected_session), tools=["isaac_sim"], limit=201,
                                   database_url=self.engine.url.render_as_string(hide_password=False))
        for row in folders:
            try:
                operation = (self.root / row["artifact"]).resolve()
                if not operation.is_relative_to(self.root):
                    continue
                if any(not (operation / name).resolve().is_relative_to(self.root)
                       for name in ("request.json", "result.json")):
                    continue
                request = json.loads((operation / "request.json").read_text(encoding="utf-8"))
                if (request.get("context") or {}).get("session_id", "") != expected_session:
                    continue
                if request.get("arguments", {}).get("action") not in ("submit", "invoke"):
                    continue
                result = json.loads((operation / "result.json").read_text(encoding="utf-8"))
                if result.get("job", {}).get("job_id") != job_id:
                    continue
                receipt = Path(result["receipt_path"]).resolve()
                if not receipt.is_relative_to(self.file.parent):
                    continue
                frozen = json.loads(receipt.with_name("request.json").read_text(encoding="utf-8"))
                saved = json.loads(receipt.read_text(encoding="utf-8"))
                if (frozen.get("endpoint") != endpoint or saved.get("job_id") != job_id or
                        not saved.get("request_id") or frozen.get("request_id") != saved["request_id"]):
                    continue
                self.remember(endpoint, SimpleNamespace(job_id=job_id, request_id=saved["request_id"]), session)
                return session, saved["request_id"]
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None
