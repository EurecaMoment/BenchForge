"""Read-only review endpoint; the authenticated Host supplies conversation scope."""

from pathlib import Path
from database.config import database_context
from storage.stage_reviews import read_current


def read_review_request(config, body):
    if not isinstance(body, dict) or set(body) - {"workspace", "session", "review_id"}:
        raise ValueError("Invalid stage review query")
    workspace = body.get("workspace")
    if not isinstance(workspace, str) or not workspace or not Path(workspace).is_absolute():
        raise ValueError("An absolute conversation workspace is required")
    with database_context(config["database_url"]):
        return read_current(workspace, body.get("session"), body.get("review_id"))
