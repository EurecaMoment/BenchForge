"""Canonical BenchForge PostgreSQL tables."""

from sqlalchemy import Boolean, Column, Float, Identity, Index, Integer
from sqlalchemy import JSON, MetaData, String, Table, Text, UniqueConstraint


metadata = MetaData()

session_documents = Table(
    "dsh_session_documents", metadata,
    Column("host_id", String, primary_key=True),
    Column("session_id", String, primary_key=True),
    Column("seq", Integer, primary_key=True),
    Column("time", Float, nullable=False),
    Column("type", String, nullable=False),
    Column("surface", String, nullable=False),
    Column("text", Text, nullable=False),
)

domain_migrations = Table(
    "benchforge_migrations", metadata,
    Column("id", String, primary_key=True),
    Column("backup_path", String),
    Column("applied_at", String),
)

runs = Table(
    "runs", metadata,
    Column("id", String, primary_key=True),
    Column("state", String, nullable=False),
    Column("plan", JSON, nullable=False),
    Column("created_at", String),
    Column("code_revision", String),
)
work_items = Table(
    "work_items", metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, nullable=False, index=True),
    Column("task_id", String),
    Column("unit", JSON),
    Column("state", String),
    Column("attempt", Integer),
    Column("lease_owner", String),
    Column("lease_expires_at", Float),
    Column("ready_at", Float),
    Column("result", JSON),
    Column("artifact", JSON),
    Column("failure", JSON),
)
events = Table(
    "events", metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("run_id", String, index=True),
    Column("work_item_id", String),
    Column("event", String),
    Column("at", String),
    Column("payload", JSON),
)
request_keys = Table(
    "benchforge_request_keys", metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, nullable=False),
)
operations = Table(
    "operations", metadata,
    Column("sequence", Integer, Identity(), primary_key=True),
    Column("workspace_id", String, nullable=False),
    Column("id", String, nullable=False),
    Column("tool", String),
    Column("state", String),
    Column("artifact", Text),
    Column("error", Text),
    Column("session_id", String),
    UniqueConstraint("workspace_id", "id", name="uq_operations_workspace_id"),
)
Index("ix_operations_workspace_sequence", operations.c.workspace_id, operations.c.sequence)
Index("ix_operations_workspace_session", operations.c.workspace_id, operations.c.session_id)
Index("ix_operations_workspace_tool", operations.c.workspace_id, operations.c.tool)
Index("ix_operations_workspace_session_sequence", operations.c.workspace_id,
      operations.c.session_id, operations.c.sequence)
Index("ix_operations_workspace_tool_sequence", operations.c.workspace_id,
      operations.c.tool, operations.c.sequence)
reviews = Table(
    "reviews", metadata,
    Column("id", Integer, Identity(), primary_key=True),
    Column("workspace_id", String, nullable=False),
    Column("session", String, nullable=False),
    Column("key", Text, nullable=False),
    Column("stage", String, nullable=False),
    Column("artifact", Text),
    Column("started", Float),
    Column("deadline", Float),
    Column("state", String),
    Column("instruction", Text),
    Column("policy_version", Integer, nullable=False, default=0),
    Column("revision", Integer, nullable=False, default=0),
    Column("progress", JSON, nullable=False, default=dict),
    UniqueConstraint("workspace_id", "session", "key", "stage", name="uq_stage_reviews_identity"),
)
Index("ix_stage_reviews_workspace_session", reviews.c.workspace_id, reviews.c.session, reviews.c.id)
legacy_review_ids = Table(
    "legacy_review_ids", metadata,
    Column("workspace_id", String, primary_key=True),
    Column("session", String, primary_key=True),
    Column("legacy_id", Integer, primary_key=True),
    Column("review_id", Integer, nullable=False),
)
legacy_events = Table(
    "legacy_events", metadata,
    Column("workspace_id", String, primary_key=True),
    Column("legacy_id", Integer, primary_key=True),
    Column("event_id", Integer, nullable=False),
)
active_plans = Table(
    "active_plans", metadata,
    Column("workspace_id", String, primary_key=True),
    Column("session", String, primary_key=True),
    Column("plan", Text),
    Column("previous", Text),
    Column("version", Integer),
    Column("pending", Boolean),
    Column("changes", JSON),
)
dataset_groups = Table(
    "dataset_groups", metadata,
    Column("workspace_id", String, primary_key=True),
    Column("id", Text, primary_key=True),
    Column("split", String, nullable=False),
)
native_jobs = Table(
    "native_jobs", metadata,
    Column("workspace_id", String, primary_key=True),
    Column("endpoint", Text, primary_key=True),
    Column("job_id", Text, primary_key=True),
    Column("session_id", String, nullable=False),
    Column("request_id", String, nullable=False),
)

