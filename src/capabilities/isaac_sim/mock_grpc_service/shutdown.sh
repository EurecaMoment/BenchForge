#!/usr/bin/env bash
# 关停本地 isaacsim_rpc.v1 gRPC mock 服务：先 SIGTERM，超时后 SIGKILL。
# 可通过 BENCHFORGE_MOCK_STATE_DIR 指定与启动脚本一致的运行状态目录。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
STATE_DIR="${BENCHFORGE_MOCK_STATE_DIR:-$ROOT/.benchforge/mock_grpc_service}"
PID_FILE="$STATE_DIR/service.pid"

if [ ! -f "$PID_FILE" ]; then
  echo "mock not running: no pid file at $PID_FILE"
  exit 0
fi

pid="$(cat "$PID_FILE")"
if ! kill -0 "$pid" 2>/dev/null; then
  echo "mock not running: stale pid $pid removed"
  rm -f "$PID_FILE"
  exit 0
fi

kill -TERM "$pid" 2>/dev/null || true
for _ in $(seq 1 50); do
  kill -0 "$pid" 2>/dev/null || break
  sleep 0.2
done

if kill -0 "$pid" 2>/dev/null; then
  echo "mock did not stop in time; sending SIGKILL to $pid"
  kill -KILL "$pid" 2>/dev/null || true
  sleep 0.5
fi

rm -f "$PID_FILE"
echo "mock stopped: pid $pid"
