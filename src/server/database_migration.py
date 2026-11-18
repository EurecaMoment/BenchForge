"""Import legacy workspace ledgers into the shared PostgreSQL service."""

import argparse
import json

from database.config import configured_database_url, resolve_database_url
from database.engine import dispose_engines
from database.import_records import migrate_workspace


def import_workspace(workspace, database_url):
    result = migrate_workspace(workspace, database_url)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--config')
    args = parser.parse_args()
    config = {}
    if args.config:
        from service_access.runtime_config import load_service_config
        config = load_service_config(args.config, required_tokens=())
    url = resolve_database_url(database_url=configured_database_url(config))
    try:
        print(json.dumps(import_workspace(args.workspace, url), ensure_ascii=False))
    finally:
        dispose_engines()


if __name__ == '__main__':
    main()
