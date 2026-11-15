# DSH integration

This package assembles one custom **BenchForge** preset with an expandable workbench.
The button opens or closes DSH's native right sidebar in the current conversation.
It preserves the session, draft, attachments and selected stage. New conversations
start with the workbench closed.

| Location | Responsibility |
| --- | --- |
| `presets/` | Compose the official standard preset, generate and install the profile |
| `host/` | Shared session scope, native session operations, handoffs and Python tool runner |
| `client/` | Browser manifest and generated bundle; editable views live in `../ui/` |
| `tools/` | Register reusable operations and built-in pipeline entry points |
| `tools/invocation/` | Resolve exact public tool names and verified workspace references before native dispatch |
| `remote/` | Host/Client contract compiled by the official Typert generator |
| `workbench/` | Host projections and Remote handlers for the single workbench |
| `production/` | Production projection, bounded progress reminders and durable review continuation |
| `build.mjs` | Generate Typert Remote artifacts and bundle Host/browser entries |
| `dist/` | Generated package entries and resources; excluded from version control |

Reusable implementations belong to [capabilities](../capabilities/). The three
built-in compositions belong to [pipelines](../pipelines/); they select steps and
acceptance policies without owning private copies of the underlying services.
Browser views belong to [ui](../ui/). See the [complete source tree](../README.md).
The browser imports Remote contracts and browser components. Host handlers own authorized
workspace access, service calls and credentials.

## Build and install

Use Python 3.11–3.13 and Node 24.9 or later. Public DSH dependencies are pinned to
`0.2.2-rc.3`; update the pins together with the target Harness version.

From the repository root:

```bash
python -m pip install -e '.[dsh,production]'
npm ci --prefix src/dsh
python src/dsh/presets/build_presets.py --package-imports --output src/dsh/cordis.patch.yml
dsh plugin --profile web add ./src/dsh
dsh --profile web --dump-config
```

`npm ci` runs the build. After editing sources, run `npm run prepare --prefix src/dsh`.
The build uses the official Typert generator and TypeScript compiler, then bundles the
Host entries and browser contribution. Run `npm pack` inside `src/dsh` to rebuild and
package all published entries and resources.

The package defaults to `python3`, `config/benchforge.local.json` and
`config/generated/benchforge-client.local.json` relative to the DSH launch directory.
Initialize and synchronize deployment configuration with `benchforge_app.py init`
and `benchforge_app.py sync`. Provider URLs and credentials belong to deployment
configuration and the launch environment.

For an existing profile, generate an overlay with `presets/build_presets.py`:
specify `--dsh-resolve-from /path/to/dsh-cli/package.json`, `--node /path/to/node`,
`--python /path/to/python`, `--config <config-file>`, `--service-config <client-config>`
and `--output <overlay-file>`. Install it with
`python src/dsh/presets/install_preset.py --profile <profile-file> --preset <overlay-file>`.

`--dsh-root <checkout>` supplies the source checkout's module lookup location.
Both forms resolve public preset exports. No private Harness source is copied or patched.
Use either the portable package or the generated overlay. The installer consolidates
the former `benchforge-ui` and `benchforge` declarations into `benchforge` while
preserving user plugins. It does not rewrite saved conversation histories.
`hot_update.py` updates deployment configuration through public DSH interfaces.

For an existing deployment, generate the updated overlay to a separate file and
merge it with `install_preset.py`; replacing the active profile drops local plugin
and Host settings. The preset reads the current directory from DSH's native
runtime context, not a `cwd` persona variable. Verify a new session's first model
turn after changing a preset, in addition to loading existing conversations.

## Runtime boundaries

- Scene tools call the configured HTTP service. FLUX, SAM3, DA3 and TRELLIS inference
  servers and weights belong to third-party deployments.
- Benchmark tools use `server.cli`; spatial learning tools use
  `server.spatial_cli`. Both record operations in the selected workspace.
- Files, shell execution, tasks, subagents, routing and conversation state use native
  DSH services. Client slots register the panel and switch; the conversation root remains native.
- Capture associations record current scene calls. Current scene tools handle background
  waiting without retired-Agent interception.
- Isaac Sim uses the upstream A100 gRPC protocol under `capabilities/isaac_sim/`.
  `benchforge_isaac_sim` uses the ordinary Python tool executor; scene capture is a
  leased backend stage. Native receipts and downloaded artifacts feed the existing
  workbench projection.
- Scene videos are saved under the configured artifacts directory's `scene-previews/`;
  browser playback reads the saved file.

The workbench displays S1 plans, S2 selected or newly created scenes, S3 collection
(including annotations), S4 questions, and S5 quality review and delivery.
Stage reviews and production decisions belong to the owning services. See
[client behavior](../ui/workbench/DESIGN.md).

Production uses a replayable `benchforgeProduction` session projection. The Host
stores pending review notices through `dsh-storage-domain`; pre-step and turn-stop
hooks request concise public progress and delivery. A review deadline resumes the
same saved plan once, including after reload. Paused or canceled work stays stopped.
The browser reads this projection and sends review actions through the existing
Remote API. It owns no continuation timer or acceptance policy.

Stage definitions live in `capabilities/benchmark_design/resources/production-stages.json`
and are shared by the planner, runner, preset and client. The Python runner requires
explicit S2 `scene.acceptance` and S3 `collection.validate`
before S4 questions and S5 packaging. Acceptance comes from the production service's
task record and captured file versions. Local `passed` files cannot approve a scene.
Derived answer files use `benchforge.native-derivation/v1`: supported deterministic
template oracles are replayed from accepted native JSON selectors, and their source
versions remain frozen between stages. Unsupported derivations stay unverified.
Missing native robot trajectories remain `needs_evidence`, and older incomplete
plans return `needs_plan_revision`. Tool identifiers are shared by registration,
prompts and UI through `capabilities/benchmark_design/resources/tool-names.json`.

S2 counts accepted scene identities, including reuse or new scene designs. Its
preview captures do not stand for production trajectories. After S2 review, S3
collects observations, native episodes and annotations and validates their sources,
alignment, allocations and partitions. `collection.validate` validates real inputs;
it does not generate missing captures. Existing `evidence.validate` plans retain
their operation IDs and `annotations.json` artifacts. New plans save `collection.json`.
The UI can preview old files but never declares S3 complete from image/JSON pairing.

The preset's `review-policy` module uses the public `ctx.tools.guard()` API to read
the durable session review before dispatch. Pending reviews allow reads, inspection
and task draft edits, and block new production submissions, shells and executable
delegation. Child sessions use their parent review. The user's Remote controls can
release a window early; the model tool cannot manufacture a user instruction or
release it itself. This is a DSH dispatch policy, not an OS sandbox or a way to
revoke jobs already submitted outside the dispatch path.

The BenchForge preset selects a native executor through `ctx.tools.setExecutor`.
It delegates scheduling, schema validation, guards, cancellation and result policy
to DSH's `ToolExecutionPipeline` and `executeToolBatch`. Only the observed
`benchmarkforge_` prefix and the declared pipeline operation IDs can resolve to
an existing, admitted canonical tool. It registers no duplicate tools and never
retries a side effect. Other unknown names retain the native error.

The current session owns the workspace: the executor supplies that parameter to
BenchForge tools and omits it from model schemas. Use relative paths for images.
For a nonexistent absolute read reference, only an exact trailing three-component
workspace anchor and an existing file inside its real root permit ancestor repair.
Existing paths, missing filenames, traversal and escaping symlinks are not repaired.
Original calls remain in session events; `meta.benchforgeInvocation` records the
resolved name and changed fields, and explicit corrections feed back to the model.
Workbench projections consume this metadata. Guidance qualification touches prose
only, preserving user content, CLI operation IDs and filesystem paths.

Run the focused regression checks from the repository root:

```bash
python -m unittest discover -s tests -v
node --test src/dsh/host/session_operations.test.mjs src/dsh/production/lifecycle.test.mjs src/dsh/workbench/artifacts/artifacts.test.mjs
node --test src/dsh/tools/invocation/*.test.mjs src/dsh/tools/benchmark/guidance.test.mjs
npm run prepare --prefix src/dsh
```

These tests use controlled receipts to verify lifecycle and validation behavior;
they do not certify simulator controllers or generated benchmark data.
