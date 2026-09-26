# Migration status

BenchForge currently migrates part of the runtime, not all functionality expressed by the original BenchClaw skills. See the [55-file inventory](SKILL_PARITY.md), also exposed by `benchforge_catalog` with `section: "migration"`.

| Area | Current implementation | Missing for reusable parity |
|---|---|---|
| Intent, literature, capability design | Generic host reasoning and saved brief | Source retrieval/read evidence, citation audit, capability pool and Q-matrix |
| Data acquisition | Collector adapters; evidence import for already prepared records | Real/benchmark source adapters, normalization and full simulator validation |
| Annotation and cleaning | SAM3/YOLOE/DA3/local VLM HTTP clients | Default batch annotation chain, review queue, Data-Juicer and deployment |
| Templates and answers | Reference registry, host-written task scripts, source selectors | GT kinship, executable template selection, asset/oracle/metric compilation |
| Quality and pilot | Basic build validation, collection counts, exact-match scoring | Answerability/anchor contracts, invalid screening, model score matrix, CDM/IRT and difficulty allocation |
| Scale-up and evaluation | Supplied-item packaging and saved-prediction scoring | Bulk synthesis, multi-model runners, custom metrics, stratified reports and DSH usage accounting |

## Intentionally removed

The mandatory five-stage sequence, nested OpenCode child-skill scheduler, tmux/DONE hierarchy, fixed model roster and checksum manifests are not part of the new mode. DSH owns the conversation and execution tools. This change does not remove the requirement to retain useful professional methods, input/output contracts, original GT and actual validation evidence.

## Reuse boundaries

Habitat/LIBERO/CARLA collectors and the template reference were adapted from BenchClaw. Isaac is an independent primitive collector, not SpatialForge's reconstruction pipeline. No SpatialForge code or configuration was modified.

Data-Juicer, IRT/CDM, the full annotation pipeline, template compiler and model runners are not bundled turnkey integrations. Saying the agent can write or invoke such code is not a completed migration. External datasets and credentials remain external.
