# From five stages to callable capabilities

The original system encoded orchestration in nested skills, numbered artifacts, ready sets, tmux sessions, child-agent identities and DONE records. The new mode retains the useful production work while letting the host harness manage conversation, context, execution and parallel agents.

| Original work | New mode |
|---|---|
| Intent, literature search/review, capability decomposition | Host reasoning, web/file tools, saved `plan` brief and task workspace |
| Simulator, real-data and benchmark source selection | Catalog, bundled collector adapters and explicit source paths |
| Templates and metric draft | Searchable original template registry; task code selects and implements the oracle |
| Three acquisition branches | Independent simulator calls; local dataset/image import through `evidence`; download/conversion through ordinary task code |
| YOLOE + VLM semantic candidate discovery | Separate `yoloe` / `llm_local` tools, with original outputs retained |
| SAM3 masks, DA3 depth | Separate `sam3` / `depthanything3` tools; inspect masks/images, reuse selected outputs |
| Semi-supervised chain | The agent composes those same tools using direct calls or PTC. No second invisible planner |
| Cleaning and source normalization | Editable task code followed by source-evidence import; predictions retain their provenance |
| Template compilation and answer program | Task code emits questions and deterministic source JSON; selectors bind items to answers |
| Pilot, invalid-item screening, IRT/CDM, scale-up | Task-selected analysis and sampling code; no compulsory rerun of unrelated stages |
| Packaging and quality checks | `build`: selected public fields, copied media, private sources/answers, collection statistics |
| Fixed model roster and evaluation reports | Saved predictions plus `evaluate`; additional models/metrics are explicit task-specific runners |

## Deliberately removed orchestration

No fixed order across five stages; no OpenCode-specific child runner; no duplicate scheduler; no required tmux/DONE hierarchy; no global preset edits; no model-written GT or model question-review gate; no mandatory target-model API calls.

The loss of a mandatory stage DAG does not remove evidence. Every mutating tool call stores its arguments, result or failure, and prior runs stay available. The model should choose corrections based on real evidence and show useful images.

## Scope of reuse

The collector algorithms and template reference were migrated from the original BenchClaw checkout. The old stage instructions are not loaded into the new system prompt. This avoids reintroducing their execution constraints.

Data-Juicer, custom template generators, IRT/CDM analysis and model API runners can be invoked as task code in their own environments. They are not presented as bundled turnkey integrations in this version. ERQA and user image datasets remain external data; the repository contains neither private images nor dataset credentials.
