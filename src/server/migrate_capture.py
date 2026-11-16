"""Explicit maintenance entry for the retired capture protocol."""

import argparse
import json
from pathlib import Path
from storage.scene_jobs.migrate_capture import migrate_capture


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    from service_access.runtime_config import load_service_config, apply_runtime_environment
    from pipelines.scene_production.service import create_store

    config = load_service_config(args.config)
    apply_runtime_environment(config)
    store = create_store(config["artifacts"], config.get("harness"), config.get("database_url"))
    try:
        print(json.dumps(migrate_capture(store), ensure_ascii=False))
    finally:
        store.repo.engine.dispose()


if __name__ == "__main__":
    main()
