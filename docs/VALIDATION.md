# Validation status

Validated on 2026-09-27. This is an initial implementation, not a completed production benchmark acceptance run.

## Verified

- Windows: six targeted core tests pass, covering source replay rather than proposed answers, public/authority separation, prediction rejection, persistent failures, missing/duplicate predictions and SAM3 HTTP protocol mapping.
- The HTTP test uses a local fixture server; it is not SAM3 inference.
- A no-install offline demo creates two items, public ZIP, source/answer bundle and a 0.5 exact-match score with one intentionally wrong prediction.
- Python wheel builds successfully; core runtime, four collectors and template registry are included.
- Actual installed DSH `dsh-v0.1.7-rc.1` preset definitions compose without changing inputs. Standard plugins remain, PTC is `both`, persistent shells have separate names and runtime inspection is retained.
- Installer tested on a disposable profile: unrelated official/custom settings remain semantically identical, a backup is created and repeated installation does not duplicate the mode.
- On Linux, the actual installed DSH `defineTool` registers 15 BenchForge tools. Catalog, template lookup, plan and status execute through the real Node-to-Python boundary. The registration context is a fixture, not a model session.
- Linux: `benchforge.py setup --dsh-root` creates a fresh project-local Python environment, installs the package and generates the mode successfully using an already built DSH checkout.
- DSH's own `--dump-config` resolves the BenchForge overlay with an isolated home.
- An isolated DSH web process with the BenchForge overlay serves an authenticated HTML page with HTTP 200. No existing DSH profile or SpatialForge files were modified. The test instance is terminated after verification.

## Still requires acceptance

- First-time clone and full DSH dependency build on a fresh machine: the current web test reused an installed, built DSH checkout.
- Interactive model session, PTC execution, attachment rendering and persistent-shell behavior in the new BenchForge mode.
- Real SAM3, YOLOE, DA3 and local VLM inference; Habitat/LIBERO/CARLA capture through the new adapters; the independent Isaac collector on the installed SDK.
- Docker build and GitHub Actions matrices.
- Production dataset question correctness, source authenticity, split leakage, collection quality, custom oracle and custom metrics.
- GitHub push requires the target repository to exist and an authenticated account with write access.

No fixture or process-exit result is presented as real GPU/simulator/benchmark acceptance. The independent Isaac collector supports only its documented primitive scene format; it is not a copy of SpatialForge's reconstruction pipeline.
