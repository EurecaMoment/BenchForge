# DSH lifecycle integration regression

Run `node tests/dsh_lifecycle/run.mjs /path/to/built/deepseek-harness` after
building the DSH checkout and `src/dsh`. The runner uses the supported Web profile,
a temporary workspace and home, and an operating-system-assigned loopback port.
It needs Python on `PATH`, or `BENCHFORGE_SMOKE_PYTHON` set to its executable.

Only the model and the Python workload are fixtures. The real Agent loop, Host
plugin, session projection, cancellation controller and subprocess provider run
together. Assertions cover ordinary chat, a reasoning-only stop before the first
plan, bounded recovery, and user cancellation through the same controller as the
Web stop button. A stalled authenticated backend response exercises paused review denial and cancellation while storage is unavailable. Cancellation must record
an aborted turn, reach idle and remove the actual child process. The isolated profile is disposed before its files are
removed. No existing conversation or production service is loaded.
