# Native Host integration gate

Run from the BenchForge repository root on the Linux staging host:

```bash
node third_party/deepseek-harness4BenchForge/node_modules/vitest/vitest.mjs run \
  --config tests/dsh_host_integration/vitest.config.mjs
```

Requirements: Node 24.9+, the pinned DSH source checkout with its development
dependencies installed (Vitest and TypeScript), its matching Linux native
`node-addon-system` package for JSONL file locking, the packaged ripgrep, Bash,
and usable bubblewrap/user namespaces. The test deliberately fails when the
real process isolation cannot run. It requires no model key, GPU or PostgreSQL.
`BENCHFORGE_HOST_TEST_ROOT` optionally chooses a writable parent directory
outside `/tmp`; by default the fixture uses a unique directory under the user's
home and removes only that fixture after stopping all its services.

The config resolves every native package to the pinned DSH **source**, including
imports originating in BenchForge `.mjs`, so an older installed `lib/` cannot
silently test a different API or create a second Cordis instance.

This gate mounts real Cordis, AgentLoop, SessionController, preset registry,
JSONL session persistence, JSON Host domain storage, workspace registry, native
tool executor, filesystem policy/provider, skills, subprocess and sandboxed
Bash. Controller test support supplies only non-exercised attachment/upload,
model-default and transport plumbing; it does not mock Agent creation, tools,
filesystem, policy, persistence or process execution. No model turn is started.

Assertions cover concurrent native Session creation with independent bound
roots, native session observation/resume lookup, owned read/write, sibling and
symlink denial, seven packaged skills and actual skill loading, trusted scope
under simultaneous tool calls despite forged arguments, and real Bash output
with no sibling file visibility. Both sessions pass the native
`ctx.sessions.flush(session)` checkpoint, and the saved revision must not be an
in-memory fallback. A separate test disposes the entire Host, recreates every
service in a new Context using the same state directory, and resumes the session
from JSONL. It verifies the original header, binding and draft survive, while the
sibling remains unreadable. Missing native flock support therefore fails this
gate instead of allowing an apparently successful memory-only session.
This is separate from PostgreSQL/RLS tests and
does not claim a complete deployed UI or GPU workflow acceptance.
