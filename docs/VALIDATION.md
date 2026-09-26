# Validation status — 2026-09-27

This is a partial migration. Real task success and original skill parity are separate claims; see [SKILL_PARITY.md](SKILL_PARITY.md).

## Live DSH + Qwen + Habitat

Host: DSH `dsh-v0.1.7-rc.1`; provider `qwen-flash`, model `Qwen/Qwen3.8-Flash-Next`. The existing deployed provider was used. Only the BenchForge preset row was installed; unrelated preset rows, including SpatialForge, were preserved. No shared service restart was required.

1. **Independent initial task**, session `session-4fc77fca-259b-4127-aeb9-bdab0d19c4af`, completed. Qwen used actual Habitat capture, image inspection, task-code generation, evidence import, build and evaluate tools. Two 14-frame captures from apartment and castle scenes supplied an eight-item A/B near/far set. Qwen corrected cross-occluder marker choices during its task. No in-session engineering hints were supplied. The task still exposed depth semantics and portable-evidence defects.
2. **Guided revision**, session `session-9ce420b5-2550-4c87-bcfa-5065267f7f42`, completed. After engineering fixes, an explicit revision prompt required conversion of camera-forward Z to Euclidean camera range and retention of raw arrays, camera calibration and oracle code in the private package. The same eight marker regions were used. All eight answers stayed unchanged, while numerical ranges changed. This round is guided repair, not another independent first-pass success.
3. **Cross-machine replay**: the revision ZIP was downloaded from Linux to Windows and extracted away from the authoring tree. `python -X utf8 scripts/verify_answers.py` recomputed all eight answers from packaged `.npy` buffers, checked marker continuity/visibility and found no missing references among 38 referenced files. The supplied complete example predictions scored 5/8 = 0.625. A/B, near/far and scene allocation are each 4:4. No Habitat installation was needed for this replay; NumPy and Pillow were used.

The 0.625 score belongs to a local heuristic example, not to Qwen answering the benchmark. This is a scoring-path smoke test, not evidence of discrimination, generalization or model ranking. The eight easy items do not validate CDM/IRT or production collection quality.

### Issues revealed and repaired in the core

- Collector `--help` now returns usage without falsely failing capture validation.
- Evidence requires a source JSON document; binary arrays are retained as private assets instead of parsed as JSON.
- Relative evidence paths are documented as relative to the input JSONL directory.
- Private raw arrays, calibration and oracle scripts are copied to authority assets; public media rejects raw privileged buffers.
- Habitat capture declares camera intrinsics and forward-Z semantics; a reusable geometry helper converts axial depth to Euclidean range.
- The DSH persona and migration catalog explicitly disclose incomplete skill coverage.

### Generated-task limitations discovered by code review

The Qwen-generated standalone scorer succeeds for the complete eight-item example, but does not properly reject duplicate IDs or penalize omitted predictions; it is not equivalent to the core evaluator for malformed inputs. Its oracle's `--report-only` path also writes intermediate source JSON despite the flag description. The unmodified generated bundle remains test evidence, not an accepted general-purpose scorer/compiler. Use the core evaluator for prediction coverage checks. Windows commands use `-X utf8` because generated scripts omit explicit file encodings.

## Targeted engineering checks

- Nine Windows core tests pass: replay rather than proposed answers, source separation, prediction-only rejection, durable failure records, missing/duplicate predictions, JSON pointers, missing backend guidance, help handling, private arrays and depth/range order reversal.
- SAM3 HTTP protocol is tested with a fixture server only; this is not real inference.
- The no-install offline demo builds a two-item fixture and scores one correct prediction at 0.5.
- Python wheel construction and inclusion of core/collector/template data were previously verified.
- Actual DSH tool definitions register 15 tools. Standard/PTC/persistent-shell/Cordis preset composition, isolated setup with an existing built DSH checkout, config resolution and authenticated web serving were verified. Live sessions used direct tools and image attachments; PTC and persistent-shell execution were not separately exercised in those sessions.
- GitHub publication via Git SSH succeeded. First-time setup still needs a fresh-machine clone and full DSH dependency build; existing DSH was reused for live validation.

## Not accepted yet

Real SAM3/YOLOE/DA3 inference and complete annotation chains; LIBERO/CARLA/Isaac live capture; first-time GPU backend installation; Docker and hosted CI; source-card acquisition and normalization; full original skill parity; reusable template/oracle/metric compiler; production leakage/coverage/difficulty/answerability gates; multi-model evaluation and CDM/IRT.

No fixture, process exit or eight-item example is presented as full-system acceptance.
