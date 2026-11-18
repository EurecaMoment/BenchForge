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
Workspace namespaces derive from canonical deployment paths; moving a workspace
is a separate explicit migration, not a silent database URL change.

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
