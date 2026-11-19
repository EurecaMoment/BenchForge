"""Read-only Host boundary for authoritative benchmark run snapshots."""

import argparse
import json

from database.config import configured_database_url
from service_access.runtime_config import load_service_config
from storage.pipeline_queries import query_pipeline_runs


def main():
    """解析受宿主约束的批量查询并返回数据库快照，不注册模型工具或写入状态。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--config')
    parser.add_argument('--sessions', default='[]')
    parser.add_argument('--runs', required=True)
    parser.add_argument('--view', choices=('summary', 'detail'), default='summary')
    parser.add_argument('--history', action='store_true')
    args = parser.parse_args()
    config = load_service_config(args.config, required_tokens=()) if args.config else {}
    rows = query_pipeline_runs(args.workspace, json.loads(args.runs), sessions=json.loads(args.sessions),
                               history=args.history, database_url=configured_database_url(config), view=args.view)
    print(json.dumps({'runs': rows}, ensure_ascii=False, separators=(',', ':')))


if __name__ == '__main__':
    main()
