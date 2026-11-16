"""Local Host query boundary for the Python-owned workspace operation index."""

import argparse
import json
from storage.operation_index import query_operations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--sessions", default="[]")
    parser.add_argument("--tools", default="[]")
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    rows = query_operations(
        args.workspace,
        sessions=json.loads(args.sessions),
        tools=json.loads(args.tools),
        history=args.history,
        limit=args.limit,
    )
    print(json.dumps({"operations": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
