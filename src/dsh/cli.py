"""Read real BenchForge status through its authenticated POST API."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 从显式参数或环境取得服务配置路径，并加载经过校验的客户端设置。
def load_config(path=None):
    path = path or os.environ.get('BENCHFORGE_CONFIG')
    if not path:
        raise ValueError('Set BENCHFORGE_CONFIG or pass --config PATH')
    from service_access.scene_service import load_service_client_config
    return load_service_client_config(path)

# 带操作者凭据读取指定运行观测；未指定运行时查询服务目录。
def status(config, run_id=None):
    token_name = config.get('operator_token_env', 'BENCHFORGE_OPERATOR_TOKEN')
    token = config.get('operator_token') or os.environ.get(token_name)
    if not token:
        raise ValueError(f'Configure operator_token or set {token_name}')
    from service_access.scene_service import post_service_json

    route, payload = ('/observe', {'run_id': run_id}) if run_id else ('/catalog', {})
    return post_service_json(config['service_url'], route, payload, token=token, timeout=30, use_proxy=True)

# 解析 DSH 命令行状态查询并以保留中文的 JSON 输出结果。
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['status'])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--run-id', help='Existing run ID, excluding .sceneN')
    args = parser.parse_args()
    print(json.dumps(status(load_config(args.config), args.run_id), ensure_ascii=False))

if __name__ == '__main__':
    main()
