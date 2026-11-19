# 源码目录与依赖边界

`src/` 将三个成熟流程作为内置 pipeline。可复用实现集中在 `capabilities/`，供流程、HTTP 和 CLI 入口调用。每个能力按实际职责命名。

| 目录 | 职责 |
| --- | --- |
| [`pipelines/`](pipelines/) | 内置流程的编排、配方、阶段验收和恢复策略。`catalog.py` 汇集场景和数据配方。 |
| [`capabilities/`](capabilities/) | 提供独立的 Python 操作及对应的领域契约和静态资源。 |
| [`server/`](server/) | HTTP、CLI、工作区和训练监控入口，负责把请求交给流程或独立能力。 |
| [`database/`](database/) | PostgreSQL 连接、统一表定义、迁移和事务；不依赖业务能力或界面。 |
| [`storage/`](storage/) | 工件、文件锁、场景作业、工作区任务、阶段审查与训练记录的持久化。 |
| [`service_access/`](service_access/) | 配置、HTTP 和 OpenAI 协议访问、请求上下文、进度记录与进程控制。 |
| [`dsh/`](dsh/) | DSH 预设、Host 服务装配、工具适配、工作台投影和 Typert Remote 契约。 |
| [`ui/`](ui/) | 工作台组件、状态和样式，以及训练监控的浏览器页面。 |

三个内置流程共享这些能力。它们各自拥有流程所需的决策和阶段状态。

| 流程 | 编排内容 |
| --- | --- |
| [`scene_production/`](pipelines/scene_production/) | 规划场景，生成资产，排队采集，验收和修复。保留场景创建、采集、细化、扩展和资产准备配方。 |
| [`benchmark/`](pipelines/benchmark/) | 应用评测计划，绑定步骤和产物，推进五个评测阶段，管理人工审查和进度。保留能力数据和数据集打包配方。 |
| [`spatial_learning/`](pipelines/spatial_learning/) | 安排训练课程，执行空间学习训练，比较同预算的 A/B/C 实验。 |

| 能力目录 | 操作与资源 |
| --- | --- |
| `asset_generation/` | 资产生成、布局参考、网格处理和资产审查。 |
| `benchmark_design/` | 评测设计契约、模板和校验。 |
| `capture/` | 采集范围、图像质量检查和通用观测证据。 |
| `data_acquisition/` | 模拟器采集器、证据导入、清洗和测量。 |
| `dataset_authoring/` | 题目契约、证据数据集、空间任务生成、打包和导出。 |
| `evaluation/` | 评测协议评分、基线与报告，以及空间任务评分和评估。 |
| `isaac_sim/` | Isaac Sim 原生 A100 gRPC、作业回执、场景编译、并发采集和证据适配。 |
| `literature/` | 研究资料提取和整理。 |
| `scene_authoring/` | SceneProgram 契约、原生资产目录、几何、机位、预检和验收。 |
| `scene_library/` | 场景目录、源策略、会话选择、预览及修复上下文。 |
| `task_catalog/` | 评测任务模板与空间能力图。 |
| `training/` | 可独立调用的模型训练执行器。 |

`capabilities/`、`storage/` 和 `service_access/` 仅依赖底层模块，禁止反向导入 `pipelines/`、`server/`、`ui/` 和 `dsh/`。
评测流程负责把活动评测计划的完成度投影到场景目录，场景目录的通用查询和文件索引在能力层完成。
模型推理由配置的 HTTP/OpenAI 提供方执行，Isaac Sim 连接和采集执行由 `capabilities/isaac_sim/` 管理。

DSH 只安装一个 `benchforge` 预设。工作台在同一会话的右侧栏展开、收起，保留草稿和执行状态。
`dsh/remote/` 声明工作台契约，`ui/workbench/` 使用据此生成的客户端调用 Host。
`ui/training_monitor/` 使用独立的训练监控 HTTP 入口。

提交和重试规则在 `pipelines/scene_production/lifecycle.py`，由同目录
`service.py` 装配到 Store；`storage/scene_jobs/` 保留事务、租约、状态转换和发布围栏。
批量预检由 `pipelines/scene_production/preflight.py` 组合
`scene_authoring/validation/` 的完整诊断与 `isaac_sim/scene_requirements.py` 的适配器限制。
HTTP 和 DSH 只传递结构化结果；缺陷分组与准入判断不进入浏览器，
场景领域也不反向依赖具体模拟器。每次预检保存输入快照，静态警告与原生验收分开。
跨流程审阅由 `pipelines/reviews/` 组合各自的产物检查，流程之间不互相回调。
模板数量的共用合同在 `capabilities/task_catalog/allocation.py`。
S4 分片分配与 S5 实际交付分别由评测的 `production_allocation.py` 和
`delivery_contract.py` 对齐活动计划。

`storage/operation_index.py` 负责工作区索引迁移与会话筛选，
`storage/native_jobs.py` 负责原生仿真作业归属，`file_lock.py` 提供跨进程文件锁。
查询入口在 `server/workspace_operations.py`；DSH Host 通过服务接口读取领域数据，不直接访问数据库表。
配置读取保持无副作用，只有可执行服务入口调用 `apply_runtime_environment`。

按 [DSH 开发规范](../docs/DSH-预设能力与编写规范.md)，预设继续通过
`defineTool` 和 `ctx.tools.register` 贡献工具；Host 拥有进程和会话生命周期，
`remote/src/` 声明阶段审阅、进度和场景产物契约，UI 通过生成的客户端读取。
业务验收不放入前端，后台资源随 Host 的 `ctx.effect` 释放。
可复制测量脚本只在能力目录维护，构建时放入 bundle 的 `dist/task_support/`。

回归测试按领域放在 `tests/`，DSH/前端行为测试与相应模块相邻。
`tests/test_source_boundaries.py` 检查底层反向依赖和 Python 模块循环，
Windows/Linux CI 同时执行 Python 和 Node 行为测试。

下面是职责索引，完整受版本控制的文件清单用 `git ls-files src` 查询。避免手维护逐文件树造成缺漏；新增模块应同时更新对应领域的职责说明。

```text
src/
├── database/              # PostgreSQL 基础设施
├── storage/               # 领域存储、权限、工件发布与权威查询
├── service_access/        # 配置、身份、外部服务访问和取消
├── capabilities/          # 独立领域能力；不导入流程或界面
│   ├── asset_generation/  # 生成执行与独立的资产查询/授权
│   ├── isaac_sim/         # 原生 gRPC、实时合同、采集与证据
│   ├── scene_authoring/   # 场景契约、几何、批量预检
│   └── scene_library/     # 场景来源、选择及工件索引
├── pipelines/             # 编排、阶段审核、恢复策略
├── server/                # 业务 HTTP/CLI 查询与提交入口
├── dsh/                   # 预设、Host、工具、Skill、工作台投影
│   ├── workbench/         # Host 装配；查询与投影分模块
│   ├── remote/            # 前后端共享 Remote 契约
│   └── skills/            # 模型可读的能力和接口说明
└── ui/                    # 展示与交互，不承担业务验收
```

公开运行配置、模型模板和容器编排统一放在仓库根 `config/`；机器私有配置也应置于部署的 `config/` 目录并排除版本控制。DSH 必需的 bundle 声明、包清单、编译配置和协议/任务数据保留各自规范位置，不与运行配置混合。

工作台运行状态经服务端查询 PostgreSQL 权威记录，任务文件是工件与下载投影。示例会话使用自带静态数据，不能以文件中的伪造完成状态覆盖真实任务。JSON 工件共用严格有限数值序列化和原子发布；访问范围与领域授权仍由各自入口验证。
