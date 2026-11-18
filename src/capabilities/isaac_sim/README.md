# Isaac Sim A100 integration

BenchForge calls the public `a100sim.v1.Simulator` service from
[IsaacSim4A100, branch A100](https://github.com/EurecaMoment/IsaacSim4A100/tree/A100).
The backend schedules capture as an ordinary leased stage. No additional
BenchForge capture daemon, device lock, callback endpoint or local Kit runtime is needed.

| Module | Responsibility |
| --- | --- |
| `grpc/simulator.proto`, generated stubs, `protocol.json` | Upstream protocol and provenance |
| `grpc/transport.py` | RPC, bounded job observation and manifest-based file transfer |
| `grpc/tasks.py` | Durable request IDs, frozen payloads and job receipts |
| `grpc/receipts.py` | Per-request and per-download ownership across subprocesses |
| `tool_api.py` | Workspace operation behind the DSH `benchforge_isaac_sim` tool |
| `guide.py` | Model-readable usage joined with the live service's discovery responses |
| `authoring.py` | Portable SceneProgram-to-USD authoring |
| `mesh_materials.py`, `usd_materials.py` | Untextured PBR factors, face materials and USD literals |
| `native_geometry.py` | Measured bounds and enabled colliders from the simulator's USD export |
| `scene_requirements.py` | Adapter capability validation before queue admission |
| `capture_stage.py` | Import, simulate, inspect, capture and publish |
| `evidence.py` | Native artifact and annotation conversion |
| `recovery.py` | Explicit resume or new-attempt policy |
| `capture_requests/` | SceneProgram request validation |

Set `providers.IsaacSim4A100.grpc` in `config/benchforge.local.json`, then
run `python benchforge_app.py sync` and the usual backend/DSH launch scripts.
Configuration version 4 migrates once to version 5, preserving model settings
and backing up retired settings. On first backend start, the old queue is
backed up and unfinished old-protocol captures require an explicit retry.
That retry creates a new scene revision and leaves previous capture paths intact.

Start with `benchforge_isaac_sim({"action":"guide"})`; use
`{"action":"guide","operation":"extension.asset.import"}` to read one operation's
published scope and schema, or `feature_id` for one catalog feature. These are
live service reads, not Git version checks or deployment-directory discovery.
A generic object schema does not describe supported fields. `AVAILABLE` means
current readiness, not task acceptance; the actual job result and artifacts remain
necessary. See [DSH–Isaac Sim interface map](../../../docs/DSH-IsaacSim-接口对照.md)
for calls, source locations and the separate deployment acceptance record.

The DSH tool reads the live catalog and uses native submit/invoke, status/wait,
cancel, upload and download operations. Submission is not completion. Reusing
a request identity requires the same payload. Disconnecting a wait does not
cancel the simulator job; cancellation must be confirmed by the service.
Job ownership is persisted by workspace, session and simulator endpoint in
`storage/native_jobs.py`. Status, wait, cancel and download verify that ownership
before contacting the simulator. Historical jobs require matching session
operation records and frozen receipts.

Native asset bounds use the CPU-only `usd-core` reader from the production extra.
The adapter measures exported points/transforms and enabled colliders, records
the export digest, and keeps that file with the capture evidence. Catalog sizes
do not certify the native bounds. This does not replace contact/support checks
or visual acceptance on the actual simulator.

Independent scenes support concurrent submission and observation, with
configurable bounded capture admission. Actual simulation and rendering
parallelism depends on the selected backend. Multiple cameras use native batches
of up to 32; additional authored views are split into further batches without
dropping views. The simulator owns synchronization inside each world.

SceneProgram capture supports portable geometry and the A100 renderer's
supported materials, lights and annotations. Read the capability catalog for
adapter limits and use the live gRPC catalog for native robot/world operations.
Unsupported requirements fail explicitly; no proxy robot, fabricated motion,
RTX claim or synthetic acceptance is substituted.

Request journals and native artifacts stay under the task attempt directory.
Publication checks the current stage owner and preserves previous captures.
UI modules consume ordinary operation receipts and scene artifacts; they do not
import gRPC, SQL or simulator code.

DSH registration remains in the preset through `defineTool` and
`ctx.tools.register`. `src/dsh/host/python_tools.mjs` owns subprocess lifetime,
cancellation and cleanup; the ordinary tool pipeline retains validation,
permissions and logging. `src/dsh/workbench/artifacts/` projects the returned
artifact references. Native operation metadata assigns their stage, including
S3 for `world.collect`, without a simulator-specific frontend protocol.

Use **Isaac Sim** in displayed text, **IsaacSim4A100** for the repository and
provider, and **isaac_sim** for the integration module and tool suffix. The old
provider spelling appears only in one-way configuration migration and its tests.
