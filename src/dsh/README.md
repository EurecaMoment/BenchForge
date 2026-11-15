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

## Runtime boundaries

- Scene tools call the configured HTTP service. FLUX, SAM3, DA3 and TRELLIS inference
  servers and weights belong to third-party deployments.
- Benchmark tools use `server.cli`; spatial learning tools use
  `server.spatial_cli`. Both record operations in the selected workspace.
- Files, shell execution, tasks, subagents, routing and conversation state use native
  DSH services. Client slots register the panel and switch; the conversation root remains native.
- Capture associations record current scene calls. Current scene tools handle background
  waiting without retired-Agent interception.
- Isaac gRPC, the local resident, process adoption and explicit mock transport remain
  under `capabilities/isaac_sim/`.
- Scene videos are saved under the configured artifacts directory's `scene-previews/`;
  browser playback reads the saved file.

The workbench displays saved plans, captures, annotations, questions and reports.
Stage reviews and production decisions belong to the owning services. See
[client behavior](../ui/workbench/DESIGN.md).

Production uses a replayable `benchforgeProduction` session projection. The Host
stores pending review notices through `dsh-storage-domain`; pre-step and turn-stop
hooks request concise public progress and delivery. A review deadline resumes the
same saved plan once, including after reload. Paused or canceled work stays stopped.
The browser reads this projection and sends review actions through the existing
Remote API. It owns no continuation timer or acceptance policy.

The Python runner requires explicit S2 scene acceptance and S3 annotation checks
before S4 questions and S5 packaging. Acceptance comes from the production service's
task record and captured file versions. Local `passed` files cannot approve a scene.
Derived answer files use `benchforge.native-derivation/v1`: supported deterministic
template oracles are replayed from accepted native JSON selectors, and their source
versions remain frozen between stages. Unsupported derivations stay unverified.
Missing native robot trajectories remain `needs_evidence`, and older incomplete
plans return `needs_plan_revision`. Tool identifiers are shared by registration,
prompts and UI through `capabilities/benchmark_design/resources/tool-names.json`.

Run the focused regression checks from the repository root:

```bash
python -m unittest discover -s tests -v
node --test src/dsh/host/session_operations.test.mjs src/dsh/production/lifecycle.test.mjs src/dsh/workbench/artifacts/artifacts.test.mjs
npm run prepare --prefix src/dsh
```

These tests use controlled receipts to verify lifecycle and validation behavior;
they do not certify simulator controllers or generated benchmark data.
