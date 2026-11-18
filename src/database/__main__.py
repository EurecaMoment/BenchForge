"""Explicit database administration; no credentials on the command line."""

import argparse
import json

from database.config import configured_database_url, resolve_database_url
from database.engine import dispose_engines, engine_for
from database.migrations import initialize_schema


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "initialize"])
    parser.add_argument("--config")
    args = parser.parse_args()
    config = {}
    if args.config:
        from service_access.runtime_config import load_service_config
        config = load_service_config(args.config, required_tokens=())
    url = resolve_database_url(database_url=configured_database_url(config))
    try:
        engine = engine_for(url)
        if args.action == "initialize":
            initialize_schema(engine)
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        result = {"database": engine.dialect.name, "status": "ok", "action": args.action}
        print(json.dumps(result, ensure_ascii=False))
    finally:
        dispose_engines()


if __name__ == "__main__":
    main()
