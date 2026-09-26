# BenchForge

Benchmark construction, annotation, simulation and evaluation as composable tools
inside [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness).
DSH owns the conversation; BenchForge supplies professional methods, executable
algorithms and artifacts. There is no mandatory five-stage scheduler.

[中文快速开始](docs/QUICKSTART.zh-CN.md) · [Production tools](docs/PRODUCTION.md) ·
[Backend setup](docs/BACKENDS.md) · [Validation](docs/VALIDATION.md) ·
[Original skill mapping](docs/SKILL_PARITY.md)

## Start

Install Python 3.10+, Git and Node.js 22.19+ or 24+:

```bash
git clone https://github.com/EurecaMoment/BenchForge.git
cd BenchForge
python benchforge.py setup
python benchforge.py start
```

Setup retrieves pinned DSH source as a third-party dependency, builds it once,
installs production dependencies and creates a project-local DSH home and preset.
Configure the model provider in DSH's UI and select **BenchForge** in a new
conversation. Reuse a built checkout with
`python benchforge.py setup --dsh-root /path/to/deepseek-harness`.

BenchForge does not need to fork DSH's main implementation. It uses the installed
tool/preset interfaces and keeps DSH as a pinned third-party dependency. No
SpatialForge dependency or configuration is used.

Run `python benchforge.py demo` for an immediate offline core check. For a visual
production example with executable templates and a portable bundle:

```bash
python -m pip install -e ".[production,research]"
python examples/production_demo.py --workspace runs/production
```

This draws four images and runs design → compilation → generation → screening →
scorer controls → packaging. No model, simulator, dataset or GPU is required.
It is a synthetic example, not a benchmark quality claim.

## Capabilities

- Search original professional methods/cards; retrieve primary papers, extract
  full text, retain citation passages, design capability/source/template/metric
  bindings and consume the resulting specification and Q-matrix.
- Import JSONL/JSON/CSV/Parquet/images, retain original labels, clean media and
  optionally run Data-Juicer on derived text. Parquet uses `.[datasets]`.
- Native Habitat, LIBERO and CARLA collectors/adapters, plus an independent Isaac
  cuboid/camera collector. Optional environments are configured separately.
- Original VLM → YOLOE → SAM3 → DA3 batch chain with intermediate artifacts,
  candidate annotations and review queue. Predictions remain predictions.
- Migrated GT kinship, image composers, template/runtime generation, pilot/full
  synthesis, programmatic screening and declared difficulty allocation.
- Deterministic recipe extensions, portable generators/scorers and native camera
  range replay from retained depth arrays and calibration.
- Real-model evaluation on public inputs, proxy controls, deterministic metrics,
  stratified reports, original CDM/Rasch proxy diagnostics and DSH usage counters.
- Separate public/authority archives and persistent operation artifacts.

All 55 original skill files have method/tool mappings; seven orchestration
wrappers are replaced by DSH. This **does not claim that every dataset, CARLA task
family or GPU environment passed real acceptance**. See the validation evidence.

## Configure real execution

Copy `config.example.json` to `config.local.json`. Fill in the deployed URL and
model ID under `models.qwen`; `token_env` names an environment variable containing
its key. Configure only the annotation services and simulator Python commands
needed by your task. DSH's conversational provider and benchmark respondents are
separate configurations; both can use your deployed Qwen endpoint.

In BenchForge mode, a starting request can be:

> Use this native Habitat capture to build 24 questions about left/right,
> above/below and camera distance. Use BenchForge's compiler and source-derived
> answers, inspect pilot images, evaluate the public package with configured
> qwen, and give me a portable reproduction package and score report.

`benchforge_catalog` provides request examples and executable template IDs.
The same tools work without DSH:

```bash
benchforge catalog --workspace runs/task
benchforge compile --workspace runs/task --input compile-request.json
benchforge status --workspace runs/task
```

Each operation retains requests, artifacts and failure logs. Reuse results and
correct task inputs as needed. An interrupted process can leave a running record;
inspect artifacts before restarting work.

## Ground truth and reproduction

Answers come from official/human labels, native simulation or deterministic
programs. Models may propose annotations or write reviewable task code; they must
not promote their own predictions into authoritative answers. This is a trusted
local workflow, not an adversarial sandbox.

Public packages contain question text, choices and visible media. Complete
packages additionally retain authority and reproduction code/inputs. Forward-Z
depth and camera range are distinguished. Missing predictions score zero;
duplicate/unknown IDs fail. A documented single `{"answer": ...}` response
envelope is accepted. Synthesis checks installed and portable scorer controls.

GPU frameworks, weights and licensed datasets are external. First-time DSH builds
and model downloads depend on the machine and network; offline examples provide
a small initial installation check. See [backend setup](docs/BACKENDS.md).

## Development

```bash
python -m pip install -e ".[production,research]"
python -m unittest discover -s tests -v
```

Apache-2.0. Adapted BenchClaw algorithms retain attribution and their license under
`vendor/benchclaw/`. See [NOTICE](NOTICE) and [dependencies](third_party/dependencies.json).
