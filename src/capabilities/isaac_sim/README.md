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
| `tool_api.py` | Workspace operation behind the DSH `benchforge_isaac_sim` tool |
| `authoring.py` | Portable SceneProgram-to-USD authoring |
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

The DSH tool reads the live catalog and uses native submit/invoke, status/wait,
cancel, upload and download operations. Submission is not completion. Reusing
a request identity requires the same payload. Disconnecting a wait does not
cancel the simulator job; cancellation must be confirmed by the service.

Independent scenes can advance concurrently, with configurable bounded capture
admission. Multiple cameras use native batches of up to 32; additional authored
views are split into further batches without dropping views. The simulator
owns synchronization inside each world.

SceneProgram capture supports portable geometry and the A100 renderer's
supported materials, lights and annotations. Read the capability catalog for
adapter limits and use the live gRPC catalog for native robot/world operations.
Unsupported requirements fail explicitly; no proxy robot, fabricated motion,
RTX claim or synthetic acceptance is substituted.

Request journals and native artifacts stay under the task attempt directory.
Publication checks the current stage owner and preserves previous captures.
UI modules consume ordinary operation receipts and scene artifacts; they do not
import gRPC, SQL or simulator code.
