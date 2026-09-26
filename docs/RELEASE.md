# Release 0.1.0 preparation

Target repository: https://github.com/EurecaMoment/BenchForge

The repository contains only the independent BenchForge implementation, original-source attribution, pinned third-party dependency metadata, examples and targeted validation. Runtime datasets, model weights, credentials, local configuration and built DSH dependencies are ignored.

User entry points:

```bash
python benchforge.py demo
python benchforge.py setup
python benchforge.py start
```

The offline demo is the fastest installation check. The full application uses DSH; a fresh machine must download/build DSH once and configure a model provider. Existing built DSH can be reused with `setup --dsh-root PATH`. Optional GPU backends are not installed by the core demo.

Before calling this production-ready, complete the outstanding acceptance items in `VALIDATION.md`, especially a fresh full setup and an actual model-operated benchmark task. This initial release should not be advertised as complete end-to-end GPU validation.

GitHub publication requires repository access. SSH authentication and repository permissions are separate: successful `ssh -T git@github.com` is not evidence that a particular private/new repository exists or is writable.
