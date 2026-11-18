# BenchForge database boundary

`database` is the only place where BenchForge creates engines, initializes the
schema, and resolves the configured database URL. Production startup requires
`postgresql+psycopg`. Every runtime and database regression test uses PostgreSQL. There is no embedded database adapter or fallback.

Feature modules own domain queries and file artifacts. They import tables from
`database.schema` and acquire pooled connections through `database.engine`.
Images, trajectories, model outputs, and other large products remain files;
PostgreSQL stores their bounded references, state, leases, and metadata.

## Ownership and configuration

- `config.py`: explicit config, request context, environment and legacy deployment
  receipt resolution, in that order. A configured but missing environment
  variable is an error. A request context never mutates process environment.
- `engine.py`: one bounded pool per URL per process; eviction and process shutdown
  dispose pools. Request objects do not dispose shared pools. Defaults: five
  persistent connections, five overflow, ten-second pool wait, five-second
  connect/lock timeout and thirty-second statement/idle-transaction timeout.
  `BENCHFORGE_DB_POOL_SIZE`, `MAX_OVERFLOW`, `POOL_TIMEOUT`, `CONNECT_TIMEOUT`
  (each with the `BENCHFORGE_DB_` prefix) override the pool/connect settings.
- `transactions.py`: rollback, atomic upserts and transaction-scoped entity
  locks. Plans and reviews lock one workspace/conversation, not all sessions.
  Scene submissions lock their idempotency key; retries use task/run row locks.
  The scene service owns a shared operator-authorized queue and scene library;
  its run IDs and request keys stay global, while DSH operation/review/plan/job
  ledgers are always scoped to the conversation workspace.
- Schema and import transactions do not include model inference or simulation.
  DSH still uses its supported subprocess execution interface; short CLI calls
  have process-local pools, not a shared pool across operating-system processes.
- `schema.py` / `migrations.py`: canonical tables, ordered schema versions and
  a PostgreSQL advisory lock for concurrent startup. Newer schemas fail closed.
- `import_records.py`: explicit streaming import of exported JSONL records. It never runs
  during ordinary PostgreSQL reads and never overwrites newer target rows.

DSH Host timers, workbench reads and tools receive the same configuration path.
The Python backend owns SQL; the Web client receives domain results only.
Review checks use `/stage-review/read` with a read-only PostgreSQL snapshot,
without mutation locks or cached approval. Reads have a deadline and are cancellable.
DSH's `sessionPersistence`, `storageDomain`, and `sessionProjections` remain
official Host interfaces. This package owns BenchForge business records, not
the internal format of DSH conversation archives or plugin storage.

## Administration

Set `BENCHFORGE_DATABASE_URL` (or a config's `database_url_env`) then run:

```bash
python -m database check
python -m database initialize
python -m server.database_migration --workspace /absolute/workspace
```

Each command accepts `--config /absolute/service.json`. Stop writers and back up
the source before import; keep the original files and import report. Imports
commit each workspace atomically and return old-to-new review ID mappings,
because old integer IDs can collide across workspaces. DSH resolves imported review aliases
through the Host reader and resumes with the canonical ID; historical session
events remain unchanged. Old scene runs, work items, events and request keys
from the export are imported too. Conflicting immutable identities abort the
transaction. The command never deletes source files.
The importer reads `database-import/<table>.jsonl` under the source workspace. Export records before upgrading; the importer has no old database driver.
Registered conversation workspaces use stable Host-assigned IDs. Older,
unregistered workspaces retain their canonical-path hash for compatibility.
`storage.access_identity.workspace_namespace` resolves trusted service/UI roots
to the registered identity without depending on a thread-local model context.
Old namespace authorization requires an explicit administrator-verified mapping;
registration never makes a former shared directory visible to every session.

## Scoped data access and asset versions

Schema versions 5 and 6 add `workspaces`, `workspace_sessions`, `assets`,
`asset_versions`, `asset_grants`, and `pipeline_documents`. Existing `session`
writers remain supported: reviews and active plans expose a generated
`session_id` read alias. Old relationship foreign keys are installed `NOT VALID`
to preserve historical rows while checking new writes; historical constraint
validation remains an explicit migration task. New JSON fields use JSONB,
and new timestamps are timezone-aware. Existing time representations are not
silently converted. There is no automatic authorization inferred from JSON files.

`database.access.AccessScope` is created from verified Host identity. Trusted
services resolve it against registered ownership, root generation and policy
epoch before access. `read_transaction` uses transaction-local settings,
read-only mode, a deadline and role checks. The fixed query API in
`storage.access_queries` accepts resource types and bounded pagination, not SQL.
Model clients never receive a connection string. PostgreSQL custom settings are
not credentials: callers with arbitrary SQL could change them, so the Host must
not expose arbitrary SQL or its service credentials.

RLS policies preserve existing owner-service writes and constrain an independent
reader. The reader must be a non-owner without administrative privileges,
BYPASSRLS, inherited ownership or write grants. Normal `benchforge_app.py sync`
and `serve` deployment creates and verifies this reader automatically: generated
credentials stay in the ignored private settings and `config/generated/database.env`;
the service configuration contains only `reader_database_url_env`, not its URL.
Startup initializes the schema, creates the absent restricted LOGIN role, grants
the fixed read surfaces and checks a real reader login before starting HTTP.

For an existing external database, set `database.reader_url_env` in
`config/benchforge.local.json` to an environment variable containing a separately
provisioned reader URL for the same database/schema. The business-service role
must own the tables so it can grant these read surfaces. Without an existing
reader, automatic creation requires CREATEROLE; missing privileges stop startup
with a specific error, without falling back to the owner connection. Repeated
startup never rotates an existing role's password or changes its ownership.

The same administration step is available independently:

```bash
python -m database initialize
python -m database provision-reader --config config/generated/benchforge-server.local.json
python -m database grant-reader --role benchforge_reader
```

`grant-reader` remains available for an administrator-created role. The provisioning
command reads credentials only from environment/configuration, never CLI arguments
or model input; its output contains no connection strings. `enforce_role=False` is
reserved for trusted internal domain calls, not an external database endpoint.
The original business-service connection remains private.

Asset versions have immutable manifests and private storage references.
Workspace, owner-library and public grants are explicit and apply to one
version. Personal-library grants are scoped to the authenticated owner, including
future sessions of that owner; content deduplication never grants permission.
Dependencies must also be authorized. Generation verifies mesh/material/texture
references, snapshots their bytes into service-private storage and publishes an
owner-library grant. Catalog and file resolution check authorization and hashes;
they do not expose the whole `generated_assets` directory to scoped sessions.
Revocation stops future access; it does not reclaim previously copied bytes.
Automatic physical garbage collection is intentionally absent.

`storage.access_documents` stores bounded service-authored pipeline documents
with compare-and-swap revisions. Local JSON files are projections; mutating them
does not update the committed document. The pipeline run namespace is distinct
from scene queue runs. Raw DSH conversations retain official persistence.

## Verification

```bash
BENCHFORGE_TEST_POSTGRES_URL=postgresql+psycopg://.../disposable_test_db \
  python -m unittest discover -s tests -v
```

The PostgreSQL tests use unique temporary workspaces and a disposable schema;
run them only against a test database. Tests create isolated PostgreSQL schemas and remove them after each test. Tests requiring a database skip explicitly when the test URL is absent.

## DSH search provider

The Host plugin `src/dsh/database/session_query.mjs` extends the official
`SessionQueryEngine`: exact reads and replay still use DSH persistence; semantic
search documents are rebuilt in PostgreSQL through authenticated backend routes.
`src/storage/session_search.py` owns SQL, namespace isolation and atomic index
replacement. Cursors bind to the indexed revisions and reject changed corpora.
The preset disables the upstream `session-query-sqlite` Loader row and installs
`benchforge-session-query`. The old name is only the compatibility target needed
to disable the upstream plugin, never an enabled provider or fallback.

The pinned DSH dependency may distribute optional database plugins. They are not
loaded by this profile. Do not delete files from an upstream dependency or remove
historical backups to disguise their existence.
