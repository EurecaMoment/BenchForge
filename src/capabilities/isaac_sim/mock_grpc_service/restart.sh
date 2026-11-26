#!/usr/bin/env bash
# 重启本地 isaacsim_rpc.v1 gRPC mock 服务：先关停，再启动。
# 端口与状态目录由 start.sh / shutdown.sh 共享的环境变量决定。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$SCRIPT_DIR/shutdown.sh"
"$SCRIPT_DIR/start.sh"
