"""Canonical BenchForge PostgreSQL tables."""

from sqlalchemy import (Boolean, CheckConstraint, Column, DateTime, Float, ForeignKey,
                        ForeignKeyConstraint, Identity, Index, Integer, func)
from sqlalchemy import Computed, MetaData, String, Table, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB as JSON


metadata = MetaData()

workspaces = Table(
    "workspaces", metadata,
    Column("workspace_id", String, primary_key=True),
    Column("host_id", String, nullable=False),
    Column("owner_id", String, nullable=False),
    Column("root_path", Text, nullable=False),
    Column("legacy_namespace", String),
    Column("root_generation", Integer, nullable=False, server_default="1"),
    Column("policy_epoch", Integer, nullable=False, server_default="1"),
    Column("state", String, nullable=False, server_default="active"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("host_id", "root_path", name="uq_workspace_host_root"),
    UniqueConstraint("workspace_id", "host_id", name="uq_workspace_host_identity"),
    CheckConstraint("root_generation > 0 AND policy_epoch > 0", name="ck_workspace_epochs"),
    CheckConstraint("state IN ('active','revoked')", name="ck_workspace_state"),
)
workspace_sessions = Table(
    "workspace_sessions", metadata,
    Column("host_id", String, primary_key=True),
    Column("session_id", String, primary_key=True),
    Column("workspace_id", String, nullable=False, unique=True),
    Column("parent_session_id", String),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["workspace_id", "host_id"], ["workspaces.workspace_id", "workspaces.host_id"],
                         name="fk_session_workspace_host"),
    ForeignKeyConstraint(["host_id", "parent_session_id"], ["workspace_sessions.host_id", "workspace_sessions.session_id"],
                         name="fk_session_parent"),
    UniqueConstraint("workspace_id", "session_id", name="uq_workspace_session_identity"),
)

assets = Table(
    "assets", metadata,
    Column("asset_id", String, primary_key=True),
    Column("workspace_id", String, nullable=False),
    Column("session_id", String, nullable=False),
    Column("name", String, nullable=False),
    Column("kind", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["workspace_id", "session_id"], ["workspace_sessions.workspace_id", "workspace_sessions.session_id"], name="fk_asset_session"),
    UniqueConstraint("asset_id", "workspace_id", name="uq_asset_owner"),
)
asset_versions = Table(
    "asset_versions", metadata,
    Column("asset_version_id", String, primary_key=True),
    Column("asset_id", String, nullable=False),
    Column("workspace_id", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("storage_ref", Text, nullable=False),
    Column("content_hash", String(64), nullable=False),
    Column("manifest", JSON, nullable=False),
    Column("dependencies", JSON, nullable=False),
    Column("metadata", JSON, nullable=False),
    Column("provenance", JSON, nullable=False, server_default="{}"),
    Column("derived_from_version_id", String, ForeignKey("asset_versions.asset_version_id", name="fk_asset_derived_version")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["asset_id", "workspace_id"], ["assets.asset_id", "assets.workspace_id"], name="fk_asset_version_owner"),
    UniqueConstraint("asset_id", "version", name="uq_asset_version"),
    UniqueConstraint("asset_version_id", "workspace_id", name="uq_asset_version_owner"),
    CheckConstraint("version > 0", name="ck_asset_version_positive"),
)
asset_grants = Table(
    "asset_grants", metadata,
    Column("grant_id", String, primary_key=True),
    Column("asset_version_id", String, nullable=False),
    Column("workspace_id", String, nullable=False),
    Column("audience", String, nullable=False),
    Column("grantee_workspace_id", String, ForeignKey("workspaces.workspace_id", name="fk_asset_grantee")),
    Column("grantee_owner_id", String),
    Column("allow_derive", Boolean, nullable=False, server_default="false"),
    Column("expires_at", DateTime(timezone=True)),
    Column("revoked_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["asset_version_id", "workspace_id"], ["asset_versions.asset_version_id", "asset_versions.workspace_id"], name="fk_asset_grant_owner"),
    UniqueConstraint("asset_version_id", "audience", name="uq_asset_grant_audience"),
    CheckConstraint("(audience = 'public' AND grantee_workspace_id IS NULL AND grantee_owner_id IS NULL) OR "
                    "(grantee_workspace_id IS NOT NULL AND grantee_owner_id IS NULL AND audience = 'workspace:' || grantee_workspace_id) OR "
                    "(grantee_owner_id IS NOT NULL AND grantee_workspace_id IS NULL AND audience = 'owner:' || grantee_owner_id)", name="ck_asset_grant_audience"),
)
Index("ix_asset_grants_recipient", asset_grants.c.grantee_workspace_id, asset_grants.c.asset_version_id)
Index("ix_asset_grants_owner", asset_grants.c.grantee_owner_id, asset_grants.c.asset_version_id)

pipeline_documents = Table(
    "pipeline_documents", metadata,
    Column("workspace_id", String, primary_key=True),
    Column("session_id", String, primary_key=True),
    Column("run_id", String, primary_key=True),
    Column("name", String, primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("document", JSON, nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(["workspace_id", "session_id"], ["workspace_sessions.workspace_id", "workspace_sessions.session_id"], name="fk_pipeline_document_session"),
    CheckConstraint("revision > 0", name="ck_pipeline_document_revision"),
)

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
    Column("workspace_id", String),
    Column("session_id", String),
    ForeignKeyConstraint(["workspace_id", "session_id"], ["workspace_sessions.workspace_id", "workspace_sessions.session_id"], name="fk_run_session"),
    CheckConstraint("(workspace_id IS NULL) = (session_id IS NULL)", name="ck_run_scope_pair"),
)
Index("ix_runs_session", runs.c.workspace_id, runs.c.session_id, runs.c.created_at, runs.c.id)
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
    Column("session_id", String, Computed("session", persisted=True)),
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
    Column("session_id", String, Computed("session", persisted=True)),
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

