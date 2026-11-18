"""Explicit database administration; no credentials on the command line."""

import argparse
import json

from database.config import configured_database_url, resolve_database_url
from database.engine import dispose_engines, engine_for
from database.migrations import initialize_schema


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["check", "initialize", "grant-reader", "provision-reader"])
    parser.add_argument("--config")
    parser.add_argument("--role", help="Existing dedicated read-only database role")
    args = parser.parse_args()
    config = {}
    if args.config:
        from service_access.runtime_config import load_service_config
        config = load_service_config(args.config, required_tokens=())
    url = resolve_database_url(database_url=configured_database_url(config))
    try:
        engine = engine_for(url)
        if args.action in ("initialize", "provision-reader"):
            initialize_schema(engine)
        if args.action == "provision-reader":
            from database.reader import provision_reader, reader_url
            provision_reader(url, reader_url(config), create=config.get('reader_provision', False))
        if args.action == "grant-reader":
            if not args.role:
                parser.error("grant-reader requires --role")
            from database.access_policies import grant_reader
            with engine.begin() as connection:
                grant_reader(connection, args.role)
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        result = {"database": engine.dialect.name, "status": "ok", "action": args.action}
        print(json.dumps(result, ensure_ascii=False))
    finally:
        dispose_engines()


if __name__ == "__main__":
    main()
