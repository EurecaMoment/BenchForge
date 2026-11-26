#!/usr/bin/env bash
# 启动本地 isaacsim_rpc.v1 gRPC mock 服务（默认 127.0.0.1:18500）。
# 运行状态写入 .benchforge/mock_grpc_service/：
#   service.pid  进程号
#   service.log  标准输出与错误
# 可通过环境变量覆盖：BENCHFORGE_MOCK_PORT、BENCHFORGE_MOCK_STATE_DIR、BENCHFORGE_PYTHON。
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"
STATE_DIR="${BENCHFORGE_MOCK_STATE_DIR:-$ROOT/.benchforge/mock_grpc_service}"
PORT="${BENCHFORGE_MOCK_PORT:-18500}"
PID_FILE="$STATE_DIR/service.pid"
LOG_FILE="$STATE_DIR/service.log"
PYTHON="${BENCHFORGE_PYTHON:-$ROOT/.venv/bin/python}"

if [ ! -x "$PYTHON" ]; then
  PYTHON="$(command -v python3 || true)"
fi
if [ -z "$PYTHON" ]; then
  echo "python3 not found; set BENCHFORGE_PYTHON" >&2
  exit 1
fi

mkdir -p "$STATE_DIR"

if [ -f "$PID_FILE" ]; then
  pid="$(cat "$PID_FILE")"
  if kill -0 "$pid" 2>/dev/null; then
    echo "mock already running: pid $pid, 127.0.0.1:$PORT"
    echo "log: $LOG_FILE"
    exit 0
  fi
  rm -f "$PID_FILE"
fi

cd "$ROOT"
PYTHONPATH_VALUE="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
if command -v setsid >/dev/null 2>&1; then
  nohup setsid env PYTHONPATH="$PYTHONPATH_VALUE" \
    "$PYTHON" -m capabilities.isaac_sim.mock_grpc_service --port "$PORT" \
    >>"$LOG_FILE" 2>&1 &
else
  nohup env PYTHONPATH="$PYTHONPATH_VALUE" \
    "$PYTHON" -m capabilities.isaac_sim.mock_grpc_service --port "$PORT" \
    >>"$LOG_FILE" 2>&1 &
fi
pid=$!
echo "$pid" >"$PID_FILE"

# 等待端口就绪，最多约 10 秒。
ready=0
for _ in $(seq 1 50); do
  if ! kill -0 "$pid" 2>/dev/null; then
    break
  fi
  if (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null; then
    exec 3>&- 2>/dev/null || true
    ready=1
    break
  fi
  sleep 0.2
done

if [ "$ready" -ne 1 ]; then
  echo "mock failed to start on 127.0.0.1:$PORT" >&2
  tail -n 20 "$LOG_FILE" >&2 || true
  if kill -0 "$pid" 2>/dev/null; then
    kill -TERM "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  exit 1
fi

echo "mock started: pid $pid, 127.0.0.1:$PORT"
echo "log: $LOG_FILE"
