# 部署配置

仓库的应用部署配置统一放在 `config/`。从 `benchforge.example.json` 初始化 `benchforge.local.json`，服务地址、模型、数据库及 Isaac Sim 端点都从这一个私有配置生成。

| 文件或目录 | 用途 |
| --- | --- |
| `benchforge.example.json` | 可提交的默认配置模板，不含真实凭据 |
| `benchforge.local.json` | 当前实例的私有设置，由 `python benchforge_app.py init` 创建 |
| `generated/` | `sync` 生成的服务端、客户端、DSH 与数据库配置，不手工复制到另一实例 |
| `dsh.env` | 当前实例的私有环境变量与凭据，不提交仓库 |
| `dsh.env.example` | 私有环境文件的变量模板；操作暂存目录必须设置在任务工作区之外 |
| `compose.yml` | 托管 PostgreSQL 的 Compose 声明，凭据由生成的私有环境提供 |
| `task-environment.requirements.txt` | DSH 专用任务 Conda 环境的基础依赖版本；不包含模型推理服务 |

`task_environment` 配置专用任务环境：`prefix` 默认 `../.benchforge/task-env`（相对 `config/`），
`conda` 为 Conda 可执行文件，`clone` 可指定已有环境的绝对路径作为离线复用来源。
`python benchforge_app.py setup` 会创建它；已有部署可运行 `bash setup-task-environment.sh`，
`bash setup-task-environment.sh --check` 只验证依赖。已有专用环境中的包不会升级，补库时锁定
已装版本；缺少兼容依赖会明确失败，不能修改共享环境来凑齐。

每个资产模型提供方的 `max_concurrent_requests` 独立限制本后端进程发往该服务的并行 HTTP 请求，默认值为 3，允许范围为 1 到 32。服务返回 429/503 时仍按现有资源等待流程处理；请求等待和执行耗时会写入后端日志，便于根据实测调整各服务上限。该限制是进程内的，不替代模型服务自身的全局队列与 GPU 配额。

检查通过后生成 `generated/task-runtime.json`，记录解释器、版本和允许读取的依赖路径。
标准启动器自动让 DSH shell 和 `benchforge_task_code` 使用该环境。自定义 tmux 启动器需对
DSH 与后端共同设置 `BENCHFORGE_TASK_RUNTIME` 为该投影的绝对路径，并将其 `python` 所在
`bin/` 加到 **DSH 进程**的 `PATH` 首位。后端继续使用原服务解释器和原 PATH，只有
`benchforge_task_code` 根据投影选择任务解释器。环境仅可读，任务仍只写本会话工作区；不会开放
仓库、第三方组件源码或整个 Conda 安装目录。

显式传入配置路径仍受支持；同机多实例可以使用各自部署目录下的 `config/`。相对配置路径以引用的服务配置所在目录为基准。Python 服务及客户端共用 `service_access/service_config.py`，DSH 的语言入口通过共同配置样例核对行为。

在 `dsh.env` 中将 `TMPDIR`、`TMP` 和 `TEMP` 指向任务工作区之外的可写目录。DSH 启动器会将这些配置传给工具进程，供受限操作创建私有暂存目录；不要在 systemd 环境中再次覆盖它们。

DSH bundle 必需的 `src/dsh/cordis.patch.yml` 属于插件声明；构建配置、协议 schema、生产配方和测试样例保留在各自模块，不混入部署设置。

BenchForge 预设按已配置模型的窗口和回答预算生成压缩策略。256K 窗口、128K 回答上限时，
`headroomTokens=32768`、摘要 `maxTokens=16384`，实际输入触发点为 98304 token（包含系统提示和工具定义）。
主模型上下文、回答上限和推理强度保持原配置。`sync` 与预设安装/更新都会补齐缺失策略；
操作者显式配置的原生 `modelPolicies` 优先，不会被生成器静默覆盖。
`benchforge-summary-policy.targetTokens` 默认为 4000，只指导检查点正文，不截断正常回答或必需的用户约束。
进度通知使用 `progressSilenceMs=60000`、`progressReminderIntervalMs=300000`；无新业务状态且未回应的同一提醒不再重复注入。
场景工具的 `reportMaxChars=6000` 为原生结果裁剪预留空间，支持 `json_pointer`、`offset`、`limit` 和 `detail=true`；
原始证据始终完整保留，概览和分页都明确标记未读部分，不能作为验收通过的依据。
