# BenchForge 工作区隔离与数据访问

状态（2026-11-19）：**本轮源码已实现，尚未完成正式部署切换与端到端验收。** Linux 阶段检查通过 265 项 Python 和 157 项 Node 测试；原生 Host 整合 2 项通过，覆盖创建、工具、文件/进程边界，以及实际 JSONL 落盘后销毁并重建 Host 的会话恢复。预览端点 `50053` 的真实工具验收通过 22 步，包含双相机采集、六种跨会话拒绝和 PostgreSQL 归属核对。不能据此声称当前 DSH 或 Isaac Sim `50051` 已运行全部新代码。

范围是 DSH 的 BenchForge 预设：会话工作区、受限执行、PostgreSQL 业务记录、生成资产复用及旧会话迁移。职责遵循 [DSH 预设能力与编写规范](DSH-预设能力与编写规范.md)：Host 管理身份、会话和插件生命周期，Python 管理业务数据，Client 消费 Remote 与投影。原始对话继续使用 DSH 官方 `SessionPersistence`，没有改成自定义业务数据库格式。

## 1. 身份与访问边界

Host 为每个新会话生成稳定的 `workspace_id`，持久保存 `host_id`、`owner_id`、`session_id`、物理根目录、`root_generation` 和 `policy_epoch`。根目录不取自模型参数。同一会话多轮续作保持绑定，新会话及子 Agent 各有独立根。

当前是**单个可信 Host 的部署所有者模型**。`owner_id` 默认是该 Host 配置的 `host-owner`，用于个人资产库授权；它不是已实现的多租户登录、组织成员或跨 Host 用户认证。没有额外认证隔离时，不得将同一配置视为多个互不信任用户的托管服务。

| 入口或模式 | 本轮实现边界 |
| --- | --- |
| BenchForge `workspace-write` | 文件与进程读取受 Host 授权根约束；工作区可写，已发布资源副本只读 |
| BenchForge `read-only` | 同样限制外部读取；文件/进程写入按 DSH 只读策略处理，不自动取消已提交的远端业务作业 |
| 用户显式选择完全权限 | 原生文件/进程限制按 DSH 完全权限语义放宽，不再承诺任意脚本无法读取部署账户的其他资源；业务 API 仍检查会话归属 |
| `benchforge_task_code` | 始终使用独立受限 Python 视图，要求可信会话身份和可用 bubblewrap，不自动回退为无隔离执行 |
| 模型数据库读取 | 固定只读业务查询，无数据库凭据、任意 SQL 或调用方自选身份 |
| 可信生产服务 | 可以按业务协议写数据库、运行仿真和发布产物，其凭据不交给任务代码 |

Host 通过正式 `agent/create`、`agent/created`、`tools/execute`、`working-directory/access` 扩展点绑定范围。服务请求携带短时签名身份票据，Python 再与 PostgreSQL 中的登记、根目录代际和权限版本核对。路径、作业 ID、`session_id` 或 JSON 所有者字段本身不能授权。

## 2. 实际目录和职责

根目录由部署配置确定；Host 默认工作区与资料根来自 `DSH_HOME`。当前没有必须建立 `published/` 或 `imported-assets/` 的约定。

```text
<DSH_HOME>/
  workspaces/<workspace-id>/
    inputs/                       会话输入
    tasks/                        可编辑草稿和任务代码
      <task-label>/work/          benchforge_task_code 的执行目录
    outputs/                      会话自建交付稿
    operations/<operation-id>/    操作展示文件副本，不是验收真值
  resources/                      Skill、任务支持文件和只读输入快照
  <官方持久化配置位置>/            原始对话及 Host domain 状态，模型不获根目录授权

<service-artifacts>/
  <task-id>/revision_<n>/          服务管理的采集与生产证据
  .generation-inputs/             资产输入的私有冻结副本
  .shared-assets/bundles/<id>/    已发布资产包

<private-deployment-state>/      凭据、数据库连接配置和运维备份
```

业务操作先在工作区外的服务私有目录执行，再通过固定目录句柄发布展示文件，防止模型用符号链接重定向服务写入。正式计划和运行文档在 PostgreSQL 保存版本；修改本地 `state.json`、`plan.json` 或手写成功回执不改变真值。

| 源码位置 | 职责 |
| --- | --- |
| [src/dsh/access/](../src/dsh/access/) | 工作区绑定、调用范围、资料发布、输入交接、迁移编排 |
| [src/dsh/host/](../src/dsh/host/) | 可信 Python 调用、私有配置、会话操作和进程生命周期 |
| [src/dsh/remote/](../src/dsh/remote/)、[workbench/](../src/dsh/workbench/) | owner Remote、投影和装配，不持有 SQL 或仿真规则 |
| [src/database/](../src/database/) | PostgreSQL 连接池、schema v6、迁移、访问事务、RLS、reader 配置 |
| [src/storage/](../src/storage/) | 领域查询、身份映射、资产版本、权威文档、受限文件 I/O 和发布 |
| [src/service_access/](../src/service_access/) | 服务上下文、身份票据、任务路径及资源文件授权 |
| [task_workspace.py](../src/server/task_workspace.py)、[task_runtime.py](../src/server/task_runtime.py) | 受限任务 Python，不挂载全部服务源码、资产或真实 HOME |
| [src/capabilities/](../src/capabilities/) | 按业务类型消费授权输入、检查依赖及发布结果 |

DSH 依赖中的通用变更提供 `readAccess`、受限文件目标、权限代际和撤销租约；不在 agent loop 中写入 BenchForge 表名、服务器路径或阶段规则。

## 3. 文件、执行和子任务

受限 Linux 进程使用明确挂载视图，提供必要系统二进制、动态库、当前 Node 与 ripgrep；其他运行资料需显式配置。不因代码位于 `third_party/` 或 Conda 就开放整个源码仓库、环境根、权重、其他会话或凭据目录。

`read`、图片读取、`write/edit`、检索、bash、终端和 PTC 原生消费入口已接入读范围。文件目标携带策略版本，失效目标拒绝继续读取；进程租约随权限撤销取消。Python 代读/代写使用 [storage/confined_files.py](../src/storage/confined_files.py) 的固定目录句柄，避免检查路径后再不受控打开文件。Linux 5.4 不依赖 Landlock 或 `openat2`；目标机仍须实测 bubblewrap 与用户命名空间。

任务代码的 `workspace_id` 只是会话内任务标签，映射到 `tasks/<label>/work`，不选择全局工作区。会话根通过固定目录句柄只读挂载，只有当前任务的 `work` 目录可写；因此可用标准文件工具返回的相同绝对路径读取本会话素材。Python 相对路径从任务执行目录开始，场景提交和资产工具的相对输入从会话根开始，不回退为后端进程目录。返回的 `workspace_path`、`conversation_path` 和产物 `source_path` 明确区分这两个位置。共享资产须先由授权接口复制为输入，不能直接读取其他会话或服务资源根。执行视图提供 Python 标准库及明确列出的数值/图像包，隔离网络，使用私有 HOME/临时目录；缺少隔离能力时失败。GPU 与生产网络使用正式工具。

子 Agent 不直接继承父目录。指定输入冻结成只读快照，准备稿通过 Host 的受限交接返回父级。阶段审核仍归父会话，草稿判断使用执行会话根。父子关系不授予任意文件读取，子准备稿不等于已验收生产产物。

部署切换仍须处理旧权限下的存量进程和终端。源码中的撤销处理和单元测试不代表已经回收正式实例的旧句柄、旧进程或远端作业。

## 4. PostgreSQL v6 和固定查询

当前 PostgreSQL 共 **20 张表**：`schema.py` 声明 19 张，`migrations.py` 单独维护 `benchforge_schema_versions`。业务运行没有 SQLite 回退。DSH 原生 JSONL 和 Host domain 继续使用官方接口，不计入这些业务表。

| 分组 | 表 |
| --- | --- |
| 迁移账本 | `benchforge_schema_versions`、`benchforge_migrations` |
| 身份 | `workspaces`、`workspace_sessions` |
| 资产 | `assets`、`asset_versions`、`asset_grants` |
| 权威文档及搜索 | `pipeline_documents`、`dsh_session_documents` |
| 运行 | `runs`、`work_items`、`events`、`benchforge_request_keys` |
| 操作与审核 | `operations`、`reviews`、`active_plans`、`native_jobs`、`dataset_groups` |
| 历史 ID 映射 | `legacy_review_ids`、`legacy_events` |

v5 增加会话身份、关系约束和 RLS；v6 增加个人库授权、资产来源及 `pipeline_documents`。JSON 字段使用 JSONB；部分历史时间字段保留原表示，没有全量转换全部旧数据。旧关联约束以 `NOT VALID` 添加，检查新增写入，但历史核对、清理及 `VALIDATE CONSTRAINT` 仍需明确迁移。

`pipeline_documents` 保存带修订号的服务文档，通过比较版本后更新拒绝覆盖并发修改。`dsh_session_documents` 是官方会话观察结果的可重建搜索索引，不替代原始对话。Host 扩展原生 `SessionQueryEngine`，精确读取和回放仍由官方持久化负责。

模型入口为 `benchforge_records(resource, limit, after)`，服务端为 `/records`，支持 `runs/tasks/events/operations/reviews/plans/native_jobs`。每页最多 100 条，返回有限字段与游标，不接受 SQL、表达式或任意表名。读取不推进审核、不放行阶段、不恢复生产。

查询使用独立 reader role，禁止管理员权限、`BYPASSRLS`、表所有权、继承写权限及 schema CREATE。只读短事务设置超时和事务级身份；RLS 检查有效工作区、会话、Host 和权限版本。连接池不保留上一事务范围。可信生产服务仍有业务写权限。

自定义 PostgreSQL 设置不是凭据：安全前提是模型拿不到连接和任意 SQL，只有可信服务设置身份。不能单独将 `current_setting()` 宣传成抗任意数据库客户端伪造的认证机制。

`benchforge_app.py sync/serve` 已接入 reader 凭据生成、schema 初始化和真实登录检查。自动创建需要服务管理员具备 CREATEROLE；外部数据库可用 `database.reader_url_env` 配置预建角色。已有密码不自动轮换，失败不回退 owner 连接。私密连接留在忽略提交的凭据及 `database.env`，服务 JSON 仅存环境变量名。命令见 [database README](../src/database/README.md)。

## 5. 共享的是完整三维资产包

本轮共享对象是**每轮完成生成并正式注册的 mesh、materials、textures 及必要依赖**，不是整个工作区、原始对话、提示词、阶段审核或隐藏答案。图片、mask、未注册临时网格和失败生成不会自动成为正式共享资产。

[asset_generation/shared_library.py](../src/capabilities/asset_generation/shared_library.py) 和 [storage/shared_assets.py](../src/storage/shared_assets.py) 负责：

1. 检查完整网格、材质和纹理引用。依赖须位于包内，缺失文件、越界引用或链接拒绝发布。
2. 冻结到服务私有 `.shared-assets/bundles/<id>/`，清单记录文件、大小与内容摘要。这些校验服务于发布版本完整性，不要求反复给所有工作文件算摘要。
3. 登记不可变 `asset_version_id`、版本、依赖版本、有限公开元数据与私有来源。数据库拒绝更新已发布版本，服务文件只读保存。
4. 自动授予生成者 `owner_id` 的个人库读取及派生权限。**同一 owner 的当前和未来会话可复用版本，无须读取原对话目录。** 知道资产 ID 不赋予其他 owner 权限。
5. 修改产生派生版本或新资产，不改写其他会话使用的版本。顶层版本授权不隐含其依赖版本的授权。

现有生成资产目录和解析链已接入授权版本；继续通过目录、导入和 SceneProgram 引用使用，不浏览共享存储根。跨会话结果不返回原会话文本、提示词、服务路径或完整私有 provenance。许可等元数据按真实来源填写，不自动推定可公开分发。

撤销或过期阻止今后的服务访问，不追回已合法复制的文件。当前没有自动物理垃圾回收或完整多用户资产管理界面。存量生成目录不经归属核对就自动发布的功能也不存在。

已有 GPU 产物的发布与新会话复用已在目标机通过：532,462 个顶点、942,725 个三角形、1 个材质、2 份纹理及 7 个文件的完整引用和 SHA-256 保持一致；发布后创建的同 owner 会话通过真实 `GenerationTools` 复用入口取得资产，其他 owner 不可见，来源会话及原始生成目录不可读。验收使用独立测试 schema 和明确选择的存量产物，没有自动迁移历史归属、重新运行 GPU 生成或验证最终渲染外观。

## 6. Isaac Sim 和服务代读

仿真服务使用 `isaacsim_rpc.v1.SimulationService` gRPC。BenchForge 校验原生作业、上传资产、retained scene/world 的会话与端点归属。知道外部 ID 不足以查询、控制、上传覆盖或下载。严格会话模式拒绝没有独立世界身份的全局机器人操作；这不代表全部原生 RPC 已实现多租户隔离。

上传先冻结输入，再检查 USD/USDZ、URDF/MJCF、glTF/GLB、OBJ/MTL 等引用。USD 只读取 layer 元数据，不先打开并遍历未授权 Stage；缺解析器时拒绝。`/World/Robot` 等 USD prim 路径按协议处理，不当作主机路径。原生产物按作业清单下载。

数据导入、brief/plan、图片处理、报告打包及资产转换的嵌套读取也接入消费者侧检查，不只检查最外层 JSON。详见 [DSH 与 Isaac Sim 接口对照](DSH-IsaacSim-接口对照.md)。正式 `50051` 尚未完成本轮切换，在线能力及 GPU 结果须在实际服务验证。

## 7. 旧会话和数据库迁移

旧会话继续可查看；没有独立绑定时不直接开放共享旧根的工具权限。owner 管理入口是 `benchforgeWorkbench` Remote 的 `workspace_migration`，步骤见 [DSH README](../src/dsh/README.md#migrate-an-existing-conversation-workspace)。模型工具和普通文件 fetch 不提供迁移。

Host 从官方会话观察与 PG 本会话 operation 归属生成候选，用户明确选择候选 ID。成功原生写入记录下的 `tasks/` 可复制为可编辑草稿；已完成本会话 operation 证据复制为只读快照。原生维护操作保持来源空闲，revision 或文件大小/mtime 变化需刷新预览。目标经 `sessionController.create` 新建，来源说明进入原生 inbox 日志；不重写旧 header 和事件。

目前最多查看最近 201 个 operation、500 个原生文件引用，不递归旧共享目录。无归属、目录引用、链接、缺失文件和工作区外证据不自动迁入，保留在旧记录/管理路径。迁移不转移任务控制、阶段通过或生产归属，也不让新会话无条件续跑旧作业。

数据库历史导入是另一明确动作：停写、备份，从 `database-import/<table>.jsonl` 显式导入，保留原文件及旧 ID 映射。普通查询不导入历史，不从可修改 JSON 推测归属。旧路径 namespace 只用于经管理确认的映射，不默认向每个会话开放。

## 8. 资料和未完成验收

预设提供七份 `src/dsh/skills/` 资料：`isaac-sim`、`fast-flux`、`fast-sam3`、`fast-da3`、`fast-trellis`、`fast-qwen`、`dspark`。Host 发布只读副本，由原生 filesystem skill provider 和 `skill` 加载。Skill 解释流程，在线 guide/catalog 给出当前参数；资料存在、注册成功、服务可用和真实验收分别记录。

正式发布前仍需完成：

- 原生 Host 持久化重启恢复已通过；图片/检索、终端/PTC、停止与撤销仍需目标机整合验证。入口见 [Host integration README](../tests/dsh_host_integration/README.md)。
- 实例停写与配置切换；核对 reader/RLS、旧会话可查看、显式迁移和新会话独立根，处理存量高权限进程。
- 已有 GPU 产物跨新会话复用已通过完整 mesh、材质、纹理、依赖哈希及来源会话隔离验证；新一轮 GPU 生成后自动发布和最终渲染外观仍需正式生产验收。
- 正式 `50051` 切换、工作台展示、取消与断线恢复回执。预览 `50053` 已通过真实 `Runtime` → `tool_api` 上传、导入、物理步进、双相机采集、下载和关闭；另一个会话的 job/world/asset 访问被拒，22 条 operation、5 条 native job、2 条资源文档的归属一致。这是合成方块接口验收，不是生产场景质量验收。
- 存量数据归属和 `NOT VALID` 历史约束核对、数据库恢复演练；回退代码不替代数据库恢复。
- 同负载下的吞吐、p50/p95、连接池/队列等待与取消耗时；功能测试不证明低延迟或无性能回退。

部署复用已有 Conda 环境和独立 tmux 启动脚本，不使用 Slurm、不升级无关依赖，运维复用已有 SSH 连接。源码实现、测试通过、正式部署、生产验收须分别记录。
