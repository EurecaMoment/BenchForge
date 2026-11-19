"""Local Host query boundary for the Python-owned workspace operation index."""

import argparse
import json
from storage.operation_index import query_operations
from database.config import configured_database_url
from service_access.runtime_config import load_service_config


# 校验命令行过滤参数，查询指定工作区的操作历史并输出 JSON。
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--config")
    parser.add_argument("--sessions", default="[]")
    parser.add_argument("--tools", default="[]")
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()
    config = load_service_config(args.config, required_tokens=()) if args.config else {}
    rows = query_operations(
        args.workspace,
        sessions=json.loads(args.sessions),
        tools=json.loads(args.tools),
        history=args.history,
        limit=args.limit,
        database_url=configured_database_url(config),
    )
    print(json.dumps({"operations": rows}, ensure_ascii=False))


if __name__ == "__main__":
    main()
