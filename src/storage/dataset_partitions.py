"""Atomically assign each source group to one immutable dataset partition."""

from contextlib import closing
from pathlib import Path
import sqlite3


def claim_partition(workspace, group, split):
    with closing(sqlite3.connect(Path(workspace) / "pipeline-groups.sqlite", timeout=30)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS groups (id TEXT PRIMARY KEY, split TEXT)")
        db.execute("INSERT OR IGNORE INTO groups VALUES (?,?)", (group, split))
        assigned = db.execute("SELECT split FROM groups WHERE id=?", (group,)).fetchone()[0]
        if assigned != split:
            raise ValueError(
                f"Scene group {group} already belongs to {assigned}; derivatives cannot cross splits"
            )
        return assigned
