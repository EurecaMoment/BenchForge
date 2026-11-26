#!/usr/bin/env bash
# 一键启动：PostgreSQL -> BenchForge 服务 -> BenchForge DSH Web
# 配置来自 config/benchforge.local.json；日志和运行记录写入 .benchforge/。
# Isaac Sim gRPC 服务不在此管理：本地 mock 或远程仿真器请单独手动启动。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PY="$ROOT/.venv/bin/python"
PRESET="$ROOT/config/generated/benchforge.local.yml"
STATE="$ROOT/.benchforge"
RUN_DIR="$STATE/run"
LOG_DIR="$STATE/logs"
SERVICE_PID_FILE="$RUN_DIR/benchforge.pid"
LEGACY_PID_FILE="$RUN_DIR/spatialforge.pid"
DSH_PID_FILE="$RUN_DIR/dsh.pid"
DB_PROJECT_FILE="$RUN_DIR/database-project"

# 首次安装使用的 pip 源与临时目录：本机默认源较慢，家目录在 CephFS 上，改用阿里云源和本地临时盘。
INSTALL_ENV=(
  "TMPDIR=${BENCHFORGE_INSTALL_TMPDIR:-/tmp}"
  "PIP_INDEX_URL=${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple/}"
  "PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST:-mirrors.aliyun.com}"
)

mkdir -p "$RUN_DIR" "$LOG_DIR"

# 加载实例私有凭据：README 约定密钥值从启动环境或 config/dsh.env 读取，
# 否则重启后端后模型提供方会因 *_API_KEY 未设置而报错或 401。
if [[ -f "$ROOT/config/dsh.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/config/dsh.env"
  set +a
fi

# 向标准错误输出启动失败原因，并以失败状态终止脚本。
die() { echo "错误: $*" >&2; exit 1; }

# 比较两个点分版本号，前者不低于后者时返回成功。
version_ge() {
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | tail -n 1)" = "$1" ]
}

# 读取配置中锁定的 DSH 字段，避免在脚本里重复常量。
locked_dsh() {
  "$PY" -c "import json;print(json.load(open('$ROOT/config/benchforge.example.json'))['dsh']['$1'])"
}

# 选择满足最低版本的 Node；当前 PATH 不满足时切换到已安装的 nvm 版本。
select_node() {
  local required current candidate
  required="$(locked_dsh node)"
  required="${required#>=}"
  if command -v node > /dev/null 2>&1 && version_ge "$(node -v | sed 's/^v//')" "$required"; then
    return 0
  fi
  while read -r candidate; do
    if version_ge "$("$candidate/node" -v | sed 's/^v//')" "$required"; then
      PATH="$candidate:$PATH"
      export PATH
      echo "  使用 Node $("$candidate/node" -v) ($candidate)"
      return 0
    fi
  done < <(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V -r)
  return 1
}

# 核对 DSH 预设与运行记录是否齐全，且版本和提交与当前锁定的子模块一致。
dsh_ready() {
  [ -f "$PRESET" ] && [ -f "$STATE/runtime.json" ] || return 1
  "$PY" - "$ROOT/config/benchforge.example.json" "$STATE/runtime.json" "$ROOT" <<'PY'
import json, subprocess, sys
from pathlib import Path

example, runtime, root = (Path(item) for item in sys.argv[1:4])
lock = json.loads(example.read_text(encoding='utf-8'))['dsh']
state = json.loads(runtime.read_text(encoding='utf-8'))
tree = subprocess.run(['git', 'ls-tree', 'HEAD', '--', lock['path']], cwd=root,
                      capture_output=True, text=True).stdout.split()
revision = tree[2] if tree else None
cli = Path(state.get('dsh_root', '')) / 'apps/cli/lib/bin.js'
ready = (state.get('dsh_version') == lock['version'] and cli.is_file()
         and state.get('dsh_revision', revision) == revision)
raise SystemExit(0 if ready else 1)
PY
}

# 首次运行时自动补齐项目环境、后端依赖与锁定版本的 DSH，后续启动直接跳过。
bootstrap() {
  local service_py
  if [ ! -x "$PY" ]; then
    echo "[0/3] 创建项目 Python 环境 .venv"
    python3 -m venv "$ROOT/.venv" || die "无法创建 $ROOT/.venv，请先安装 Python 3.11-3.13"
  fi
  service_py="$("$PY" benchforge_app.py setting backend.python)"
  # 用真实依赖导入判断环境是否装完，避免把只创建了解释器的半成品当成可用。
  if [ ! -x "$service_py" ] || ! "$service_py" -c 'import yaml, numpy, PIL, sqlalchemy, psycopg, trimesh, pycocotools, grpc, pxr' > /dev/null 2>&1; then
    echo "[0/3] 安装后端依赖到 $service_py"
    env "${INSTALL_ENV[@]}" "$PY" benchforge_app.py setup || die "后端依赖安装失败，见上方输出"
  fi
  # BenchForge DSH 插件要先由 npm ci 编译出 src/dsh/dist，预设才能加载。
  if [ ! -f "$ROOT/src/dsh/dist/benchmark-tools.mjs" ]; then
    echo "[0/3] 构建 BenchForge DSH 插件（src/dsh）"
    select_node || die "DSH 需要 Node $(locked_dsh node)，请安装后重试"
    ( cd "$ROOT" && env "${INSTALL_ENV[@]}" npm ci --prefix src/dsh ) || die "DSH 插件构建失败，见上方输出"
  fi
  if dsh_ready && "$PY" -c 'import yaml' > /dev/null 2>&1; then
    return 0
  fi
  echo "[0/3] 初始化 DSH $(locked_dsh version)（首次运行需要下载并构建，耗时较长）"
  select_node || die "DSH 需要 Node $(locked_dsh node)，请安装后重试"
  # BENCHFORGE_DSH_ROOT 可复用同版本已构建好的 DSH，跳过下载与前端构建。
  if [ -n "${BENCHFORGE_DSH_ROOT:-}" ]; then
    env "${INSTALL_ENV[@]}" "$PY" benchforge_app.py dsh-setup --dsh-root "$BENCHFORGE_DSH_ROOT" || die "DSH 初始化失败，见上方输出"
  else
    env "${INSTALL_ENV[@]}" "$PY" benchforge_app.py dsh-setup || die "DSH 初始化失败，见上方输出"
  fi
}

bootstrap
SERVICE_PY="$("$PY" benchforge_app.py setting backend.python)"

# 检查指定TCP端口是否正在监听，供启动和健康等待流程判断。
port_open() { ss -ltn 2>/dev/null | grep ":$1 " > /dev/null; }

# 核对已保存的进程编号；进程仍存活但端口未就绪时要求重启处理。
check_previous_process() {
  local name="$1" pidfile="$2" port="$3" pid
  if [ -f "$pidfile" ]; then
    pid="$(<"$pidfile")"
    if [[ ! "$pid" =~ ^[1-9][0-9]*$ ]]; then
      die "$name 的 PID 记录无效"
    fi
    if kill -0 "$pid" 2>/dev/null && ! port_open "$port"; then
      die "$name 仍在运行，端口 $port 未监听，请执行 ./restart.sh"
    fi
  fi
}

WEB_PORT="$("$PY" benchforge_app.py setting web.port)"
SERVICE_PORT="$("$PY" benchforge_app.py setting server.port)"
if [[ ! "$WEB_PORT" =~ ^[1-9][0-9]*$ || ! "$SERVICE_PORT" =~ ^[1-9][0-9]*$ ]]; then
  die "无法读取有效的 Web 和 BenchForge 端口"
fi
check_previous_process "DSH Web" "$DSH_PID_FILE" "$WEB_PORT"
check_previous_process "BenchForge" "$SERVICE_PID_FILE" "$SERVICE_PORT"
if [ -f "$LEGACY_PID_FILE" ]; then
  LEGACY_PID="$(<"$LEGACY_PID_FILE")"
  if [[ "$LEGACY_PID" =~ ^[1-9][0-9]*$ ]] && kill -0 "$LEGACY_PID" 2>/dev/null; then
    die "旧版服务仍在运行，请执行 ./restart.sh 完成升级"
  fi
fi

PREVIOUS_PROJECT=""
if [ -f "$DB_PROJECT_FILE" ]; then
  PREVIOUS_PROJECT="$(<"$DB_PROJECT_FILE")"
else
  PREVIOUS_PROJECT="$("$PY" benchforge_app.py setting database.previous_project)"
fi
if [ -n "$PREVIOUS_PROJECT" ]; then
  DB_MANAGED="$("$PY" benchforge_app.py setting database.managed)"
  DB_PROJECT="$("$PY" benchforge_app.py setting database.project)"
  if [ "$DB_MANAGED" != true ] || [ "$DB_PROJECT" != "$PREVIOUS_PROJECT" ]; then
    die "数据库项目或部署方式已修改，请执行 ./restart.sh 切换"
  fi
fi
"$PY" benchforge_app.py sync

"$PY" benchforge.py start --port "$WEB_PORT" --config-synced --check-config

# 每秒检查一次端口，在指定秒数内监听成功则返回，否则返回失败。
wait_port() {
  local port="$1" timeout="$2" i
  for ((i = 0; i < timeout; i++)); do
    port_open "$port" && return 0
    sleep 1
  done
  return 1
}

# DSH 绑定端口后还要等 Loader 装载完成才打印 token；卡住时 token 永远不出现。
# 统计DSH日志中的令牌就绪行；首次启动尚无日志时返回零。
token_line_count() {
  local count
  count="$(grep -c 'dsh web: .*token=' "$LOG_DIR/dsh.log" 2>/dev/null || true)"
  # The log does not exist on the first installation.
  printf '%s\n' "${count:-0}"
}

# 等待令牌日志行数超过启动前计数，确认本次加载器已完成初始化。
wait_new_token() {
  local before="$1" timeout="$2" i
  for ((i = 0; i < timeout; i++)); do
    [ "$(token_line_count)" -gt "$before" ] && return 0
    sleep 1
  done
  return 1
}

# 从当前DSH日志提取最后一个匹配配置端口的本机令牌访问地址。
latest_url() {
  grep -oE "http://127.0.0.1:${WEB_PORT}/?\\?token=[A-Za-z0-9_-]+" "$LOG_DIR/dsh.log" 2>/dev/null | tail -n 1 || true
}

# 请求给定地址并返回HTTP状态码，用于验证令牌访问地址已经可用。
url_status() { curl -s -o /dev/null -w '%{http_code}' "$1" 2>/dev/null || true; }

# 向失败启动的进程组或进程发送终止信号，并移除该启动尝试的PID记录。
fail_start() {
  local pidfile="$1" pid
  pid="$(<"$pidfile")"
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM -- "$pid" 2>/dev/null || true
  rm -f "$pidfile"
}

DB_MANAGED="$("$PY" benchforge_app.py setting database.managed)"
if [ "$DB_MANAGED" = true ]; then
  DB_PROJECT="$("$PY" benchforge_app.py setting database.project)"
  echo "[1/3] 启动 PostgreSQL ($DB_PROJECT)"
  printf '%s\n' "$DB_PROJECT" > "$DB_PROJECT_FILE"
  "$PY" benchforge_app.py db --project "$DB_PROJECT"
elif [ "$DB_MANAGED" = false ]; then
  echo "[1/3] 使用外部 PostgreSQL"
  : > "$DB_PROJECT_FILE"
else
  die "无法读取数据库部署方式"
fi

echo "[2/3] 启动 BenchForge 服务 (端口 $SERVICE_PORT)"
if port_open "$SERVICE_PORT"; then
  echo "  端口已监听，保留现有进程"
else
  echo "==== $(date '+%F %T') start ====" >> "$LOG_DIR/benchforge.log"
  # setsid 脱离当前终端会话，关闭终端后服务继续运行
  setsid "$PY" "$ROOT/benchforge_app.py" serve >> "$LOG_DIR/benchforge.log" 2>&1 < /dev/null &
  echo $! > "$SERVICE_PID_FILE"
  if ! wait_port "$SERVICE_PORT" 90; then
    tail -n 20 "$LOG_DIR/benchforge.log" >&2 || true
    fail_start "$SERVICE_PID_FILE"
    die "BenchForge 在 90 秒内未监听 $SERVICE_PORT，见 $LOG_DIR/benchforge.log"
  fi
  echo "  已启动 (PID $(<"$SERVICE_PID_FILE"))"
fi

echo "[3/3] 启动 BenchForge DSH Web (端口 $WEB_PORT)"
if port_open "$WEB_PORT"; then
  echo "  端口已监听，保留现有进程"
else
  TOKEN_BEFORE="$(token_line_count)"
  echo "==== $(date '+%F %T') start ====" >> "$LOG_DIR/dsh.log"
  setsid "$PY" "$ROOT/benchforge.py" start --port "$WEB_PORT" --config-synced --no-open >> "$LOG_DIR/dsh.log" 2>&1 < /dev/null &
  echo $! > "$DSH_PID_FILE"
  if ! wait_port "$WEB_PORT" 240; then
    tail -n 20 "$LOG_DIR/dsh.log" >&2 || true
    fail_start "$DSH_PID_FILE"
    die "DSH 在 240 秒内未监听 $WEB_PORT，见 $LOG_DIR/dsh.log"
  fi
  # 端口监听不等于启动完成：首次启动还要导入约 2100 个示例文件，CephFS 上很慢。
  echo "  等待 DSH 完成初始化（首次会导入示例资产，可能需要数分钟）..."
  if ! wait_new_token "$TOKEN_BEFORE" 1200; then
    tail -n 20 "$LOG_DIR/dsh.log" >&2 || true
    echo "诊断: DSH 端口已监听，但 1200 秒内没有输出新的 token，服务仍卡在启动阶段。" >&2
    echo "  inotify watches 上限=$(cat /proc/sys/fs/inotify/max_user_watches 2>/dev/null || echo unknown)" >&2
    echo "  hmr 拿不到文件监视器会导致永不完成启动，请提高上限后重试：" >&2
    echo "    sudo sysctl -w fs.inotify.max_user_watches=524288" >&2
    fail_start "$DSH_PID_FILE"
    die "DSH 未完成启动，已停止该进程以免占用端口，见 $LOG_DIR/dsh.log"
  fi
  echo "  已启动 (PID $(<"$DSH_PID_FILE"))"
fi

echo
echo "验证："
if "$PY" benchforge_app.py status > /dev/null 2>&1; then
  echo "  数据库 / 服务 / 令牌：正常"
else
  die "数据库 / BenchForge 服务 / DSH 令牌健康检查失败；未报告启动成功。请查看 $LOG_DIR 下的服务日志并修复后重试。"
fi

URL="$(latest_url)"
WEB_STATUS=""
[ -n "$URL" ] && WEB_STATUS="$(url_status "$URL")"
if [ -z "$URL" ]; then
  die "未找到 DSH Web token 地址，未报告启动成功；请检查 $LOG_DIR/dsh.log"
fi
if [ "$WEB_STATUS" != "200" ] && [ "$WEB_STATUS" != "303" ]; then
  die "DSH Web token readiness 检查返回 $WEB_STATUS，未报告启动成功；请检查 $LOG_DIR/dsh.log"
fi
echo
echo "启动完成。"
echo "  服务端: http://127.0.0.1:$SERVICE_PORT"
echo "  Web:    ${URL:-http://127.0.0.1:$WEB_PORT/ （token 见 .benchforge/logs/dsh.log）}"
echo "  日志:   $LOG_DIR"
echo "  停止:   ./shutdown.sh"
echo
# --- 浏览器操作指引（重启翻车 99% 是旧 tab 没关）---
HAS_SESSIONS=0
[ -d "$STATE/dsh-home/sessions" ] && HAS_SESSIONS=1
if [ "$HAS_SESSIONS" -eq 1 ]; then
  echo "📋 浏览器操作：直接刷新你现有的 DSH tab 即可（session 文件保留了）"
else
  echo "⚠️  浏览器操作：关掉所有旧的 DSH tab，用上面的 Web URL 重新打开！"
  echo "   （dsh-home/sessions 已被清理，旧 tab 会报 ENOENT）"
fi
echo "   不想关 tab? 只要你没手动 rm dsh-home，下次重启就能刷新续上"
