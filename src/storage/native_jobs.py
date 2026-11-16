"""Local session ownership of native jobs; remote credentials remain service-owned."""

from contextlib import closing
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from storage.operation_index import ensure_schema


class NativeJobs:
    def __init__(self, workspace):
        self.root = Path(workspace).resolve()
        self.file = self.root / ".benchforge" / "isaac_sim" / "ownership.sqlite"
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs "
                "(endpoint TEXT, job_id TEXT, session_id TEXT NOT NULL, request_id TEXT NOT NULL, "
                "PRIMARY KEY(endpoint,job_id))"
            )

    def connect(self):
        return sqlite3.connect(self.file, timeout=30)

    def remember(self, endpoint, job, session):
        with closing(self.connect()) as db, db:
            db.execute(
                "INSERT OR IGNORE INTO jobs VALUES (?,?,?,?)",
                (endpoint, job.job_id, session, job.request_id),
            )
            row = db.execute(
                "SELECT session_id,request_id FROM jobs WHERE endpoint=? AND job_id=?",
                (endpoint, job.job_id),
            ).fetchone()
            if row != (session, job.request_id):
                raise PermissionError(
                    "Native job is already associated with another session or request"
                )

    def require(self, endpoint, job_id, session):
        with closing(self.connect()) as db:
            row = db.execute(
                "SELECT session_id,request_id FROM jobs WHERE endpoint=? AND job_id=?",
                (endpoint, job_id),
            ).fetchone()
        if not row:
            row = self._recover_receipt(endpoint, job_id, session)
        if not row or row[0] != session:
            raise PermissionError(
                "Native job is not registered to this session and simulator endpoint"
            )
        return row[1]

    def _recover_receipt(self, endpoint, job_id, session):
        """Migrate old jobs only from matching session operations and frozen receipts."""
        index = self.root / "runs.sqlite"
        if not index.is_file():
            return None
        with closing(sqlite3.connect(index, timeout=30)) as db:
            ensure_schema(db, self.root)
            folders = db.execute(
                "SELECT artifact FROM operations WHERE tool='isaac_sim' AND session_id=?",
                ("" if session == "local-cli" else session,),
            ).fetchall()
        for (folder,) in folders:
            try:
                operation = (self.root / folder).resolve()
                if not operation.is_relative_to(self.root):
                    continue
                request = json.loads((operation / "request.json").read_text(encoding="utf-8"))
                if (request.get("context") or {}).get("session_id", "") != (
                    "" if session == "local-cli" else session
                ):
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
                if (
                    frozen.get("endpoint") != endpoint
                    or saved.get("job_id") != job_id
                    or not saved.get("request_id")
                    or frozen.get("request_id") != saved["request_id"]
                ):
                    continue
                self.remember(
                    endpoint,
                    SimpleNamespace(job_id=job_id, request_id=saved["request_id"]),
                    session,
                )
                return session, saved["request_id"]
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None
