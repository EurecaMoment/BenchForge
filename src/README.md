# 源码目录与依赖边界

`src/` 将三个成熟流程作为内置 pipeline。可复用实现集中在 `capabilities/`，供流程、HTTP 和 CLI 入口调用。每个能力按实际职责命名。

| 目录 | 职责 |
| --- | --- |
| [`pipelines/`](pipelines/) | 内置流程的编排、配方、阶段验收和恢复策略。`catalog.py` 汇集场景和数据配方。 |
| [`capabilities/`](capabilities/) | 提供独立的 Python 操作及对应的领域契约和静态资源。 |
| [`server/`](server/) | HTTP、CLI、工作区和训练监控入口，负责把请求交给流程或独立能力。 |
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
跨流程审阅由 `pipelines/reviews/` 组合各自的产物检查，流程之间不互相回调。
模板数量的共用合同在 `capabilities/task_catalog/allocation.py`。
S4 分片分配与 S5 实际交付分别由评测的 `production_allocation.py` 和
`delivery_contract.py` 对齐活动计划。

`storage/operation_index.py` 负责工作区索引迁移与会话筛选，
`storage/native_jobs.py` 负责原生仿真作业归属，`file_lock.py` 提供跨进程文件锁。
查询入口在 `server/workspace_operations.py`；DSH Host 不直接读取 SQLite 表结构。
配置读取保持无副作用，只有可执行服务入口调用 `apply_runtime_environment`。

按 [DSH 开发规范](../docs/DSH-预设能力与编写规范.md)，预设继续通过
`defineTool` 和 `ctx.tools.register` 贡献工具；Host 拥有进程和会话生命周期，
`remote/src/` 声明阶段审阅、进度和场景产物契约，UI 通过生成的客户端读取。
业务验收不放入前端，后台资源随 Host 的 `ctx.effect` 释放。
可复制测量脚本只在能力目录维护，构建时放入 bundle 的 `dist/task_support/`。

回归测试按领域放在 `tests/`，DSH/前端行为测试与相应模块相邻。
`tests/test_source_boundaries.py` 检查底层反向依赖和 Python 模块循环，
Windows/Linux CI 同时执行 Python 和 Node 行为测试。

下面列出全部受版本控制的源码和资源，包括随仓库分发的 `dsh/client/client.js`。
依赖安装目录、Typert 生成目录 `lib/`、打包输出 `dist/` 和运行缓存省略。

```text
src/
├── capabilities/
│   ├── asset_generation/
│   │   ├── __init__.py
│   │   ├── asset_review.py
│   │   ├── gaussian_asset.py
│   │   ├── generation.py
│   │   ├── generation_stage.py
│   │   ├── layout.py
│   │   ├── mesh_appearance.py
│   │   └── mesh_export.py
│   ├── benchmark_design/
│   │   ├── resources/
│   │   │   ├── assessment_template.json
│   │   │   ├── production-stages.json
│   │   │   └── tool-names.json
│   │   ├── __init__.py
│   │   ├── assessment.py
│   │   ├── contracts.py
│   │   └── stages.py
│   ├── capture/
│   │   ├── __init__.py
│   │   ├── capture_scope.py
│   │   ├── observation.py
│   │   ├── operator_evidence.py
│   │   └── quality.py
│   ├── data_acquisition/
│   │   ├── collectors/
│   │   │   ├── __init__.py
│   │   │   ├── carla.py
│   │   │   ├── habitat.py
│   │   │   ├── isaac.py
│   │   │   └── libero.py
│   │   ├── measurements/
│   │   │   ├── README.md
│   │   │   ├── __init__.py
│   │   │   ├── geometry.py
│   │   │   ├── public_geometry_cache.py
│   │   │   ├── public_measurements.py
│   │   │   └── public_sensor_cache.py
│   │   ├── __init__.py
│   │   ├── capture_adapter.py
│   │   ├── evidence.py
│   │   ├── preparation.py
│   │   └── run_collector.py
│   ├── dataset_authoring/
│   │   ├── scene_capture/
│   │   │   ├── __init__.py
│   │   │   ├── product_diagnostics.py
│   │   │   ├── product_packaging.py
│   │   │   ├── products.py
│   │   │   └── spatial_tasks.py
│   │   ├── spatial/
│   │   │   ├── task_generators/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── geometry.py
│   │   │   │   ├── graph.py
│   │   │   │   ├── motion.py
│   │   │   │   └── reasoning.py
│   │   │   ├── __init__.py
│   │   │   ├── conditions.py
│   │   │   ├── environments.py
│   │   │   ├── export.py
│   │   │   ├── generation.py
│   │   │   └── scenes.py
│   │   ├── __init__.py
│   │   ├── adapters.py
│   │   ├── answer_contracts.py
│   │   ├── evidence_dataset.py
│   │   ├── production.py
│   │   ├── quality.py
│   │   ├── quantity.py
│   │   ├── question_writing.py
│   │   ├── source_binding.py
│   │   └── trajectory_evidence.py
│   ├── evaluation/
│   │   ├── benchmark/
│   │   │   ├── __init__.py
│   │   │   ├── evidence_scores.py
│   │   │   ├── protocol_scores.py
│   │   │   └── reports.py
│   │   ├── spatial/
│   │   │   ├── __init__.py
│   │   │   ├── evaluation.py
│   │   │   └── scoring.py
│   │   └── __init__.py
│   ├── isaac_sim/
│   │   ├── capture_requests/
│   │   │   ├── __init__.py
│   │   │   ├── camera_trajectory.py
│   │   │   ├── robot_contact.py
│   │   │   └── trajectory.py
│   │   ├── grpc/
│   │   │   ├── UPSTREAM-LICENSE
│   │   │   ├── __init__.py
│   │   │   ├── protocol.json
│   │   │   ├── receipts.py
│   │   │   ├── simulator.proto
│   │   │   ├── simulator_pb2.py
│   │   │   ├── simulator_pb2_grpc.py
│   │   │   ├── tasks.py
│   │   │   └── transport.py
│   │   ├── LICENSE
│   │   ├── README.md
│   │   ├── __init__.py
│   │   ├── authoring.py
│   │   ├── capture_stage.py
│   │   ├── evidence.py
│   │   ├── mesh_materials.py
│   │   ├── native_geometry.py
│   │   ├── recovery.py
│   │   ├── scene_requirements.py
│   │   ├── tool_api.py
│   │   └── usd_materials.py
│   ├── literature/
│   │   ├── __init__.py
│   │   └── research.py
│   ├── scene_authoring/
│   │   ├── cameras/
│   │   │   ├── __init__.py
│   │   │   ├── layout.py
│   │   │   └── source.py
│   │   ├── catalog/
│   │   │   ├── __init__.py
│   │   │   ├── capabilities.py
│   │   │   ├── environments.py
│   │   │   ├── native.py
│   │   │   └── operators.py
│   │   ├── contracts/
│   │   │   ├── __init__.py
│   │   │   ├── robot_actions.py
│   │   │   ├── scene_design.py
│   │   │   └── scene_program.py
│   │   ├── geometry/
│   │   │   ├── __init__.py
│   │   │   ├── mesh.py
│   │   │   ├── native_asset_facing.py
│   │   │   ├── parts.py
│   │   │   ├── preview.py
│   │   │   ├── relations.py
│   │   │   └── support_graph.py
│   │   ├── resources/
│   │   │   ├── material_previews/
│   │   │   │   ├── bamboo_desktop.jpg
│   │   │   │   ├── fabric_grid.jpg
│   │   │   │   ├── grass.jpg
│   │   │   │   ├── office_wall.jpg
│   │   │   │   ├── rubber_woven.jpg
│   │   │   │   ├── sidewalk_concrete.jpg
│   │   │   │   ├── weathered_asphalt.jpg
│   │   │   │   ├── wood_laminate.jpg
│   │   │   │   └── wood_oak_floor.jpg
│   │   │   ├── native_asset_facing.json
│   │   │   ├── native_assets.json
│   │   │   └── native_materials.json
│   │   ├── validation/
│   │   │   ├── __init__.py
│   │   │   ├── acceptance.py
│   │   │   ├── policy.py
│   │   │   ├── preflight.py
│   │   │   ├── program.py
│   │   │   ├── quality.py
│   │   │   ├── request_admission.py
│   │   │   └── review_reference.py
│   │   ├── __init__.py
│   │   └── program_submission.py
│   ├── scene_library/
│   │   ├── __init__.py
│   │   ├── acceptance.py
│   │   ├── episodes.py
│   │   ├── observations.py
│   │   ├── preview_manifest.py
│   │   ├── repair_context.py
│   │   ├── scene_library.py
│   │   ├── scene_preview_video.py
│   │   └── source_policy.py
│   ├── task_catalog/
│   │   ├── resources/
│   │   │   ├── benchmark_templates.json
│   │   │   ├── capabilities.json
│   │   │   ├── capability_graph.json
│   │   │   └── spatial_templates.json
│   │   ├── __init__.py
│   │   ├── allocation.py
│   │   ├── benchmark.py
│   │   ├── build_spatial_registry.py
│   │   ├── execution_support.py
│   │   └── spatial.py
│   ├── training/
│   │   ├── __init__.py
│   │   └── solver.py
│   └── __init__.py
├── dsh/
│   ├── client/
│   │   ├── client.js
│   │   ├── index.js
│   │   └── package.json
│   ├── host/
│   │   ├── python_process.mjs
│   │   ├── python_process.test.mjs
│   │   ├── python_tools.mjs
│   │   ├── session_family.mjs
│   │   ├── session_operations.mjs
│   │   ├── session_operations.test.mjs
│   │   ├── session_scope.mjs
│   │   ├── stage_reviews.mjs
│   │   ├── stage_reviews.test.mjs
│   │   ├── subagent_handoff.mjs
│   │   ├── tool_credentials.mjs
│   │   └── tool_credentials.test.mjs
│   ├── presets/
│   │   ├── build_preset.py
│   │   ├── build_presets.py
│   │   ├── hot_update.py
│   │   ├── install_preset.py
│   │   ├── preset_definition.py
│   │   ├── profile_migration.py
│   │   └── subagent_policy.mjs
│   ├── production/
│   │   ├── collection.mjs
│   │   ├── lifecycle.mjs
│   │   ├── lifecycle.test.mjs
│   │   ├── projection.mjs
│   │   ├── review_policy.mjs
│   │   ├── review_policy.test.mjs
│   │   ├── reviews.mjs
│   │   ├── stages.mjs
│   │   ├── stages.test.mjs
│   │   └── types.d.ts
│   ├── remote/
│   │   ├── src/
│   │   │   ├── index.ts
│   │   │   └── types.ts
│   │   ├── contracts.test.mjs
│   │   ├── package.json
│   │   └── tsconfig.host.json
│   ├── tools/
│   │   ├── benchmark/
│   │   │   ├── delivery/
│   │   │   │   └── delivery_files.mjs
│   │   │   ├── catalog.mjs
│   │   │   ├── contracts.mjs
│   │   │   ├── guidance.test.mjs
│   │   │   ├── names.mjs
│   │   │   ├── preparation.mjs
│   │   │   └── tools.mjs
│   │   ├── invocation/
│   │   │   ├── executor.mjs
│   │   │   ├── executor.test.mjs
│   │   │   ├── identifiers.mjs
│   │   │   ├── workspace.mjs
│   │   │   └── workspace.test.mjs
│   │   ├── scene/
│   │   │   ├── capture/
│   │   │   │   ├── capture_associations.mjs
│   │   │   │   ├── capture_runtime.mjs
│   │   │   │   ├── capture_watch.mjs
│   │   │   │   ├── continuation.mjs
│   │   │   │   ├── continuation.test.mjs
│   │   │   │   ├── continuation_state.mjs
│   │   │   │   ├── quality_review.mjs
│   │   │   │   ├── review_images.mjs
│   │   │   │   └── submission.mjs
│   │   │   ├── contracts.mjs
│   │   │   ├── queries.mjs
│   │   │   ├── request_identity.mjs
│   │   │   ├── service_client.mjs
│   │   │   ├── tool_names.mjs
│   │   │   └── tools.mjs
│   │   └── spatial/
│   │       ├── contracts.mjs
│   │       └── tools.mjs
│   ├── workbench/
│   │   ├── artifacts/
│   │   │   ├── artifacts.test.mjs
│   │   │   ├── files.mjs
│   │   │   ├── projection.mjs
│   │   │   └── scope.mjs
│   │   ├── projections/
│   │   │   ├── preparation_products.mjs
│   │   │   ├── public_artifacts.mjs
│   │   │   └── routes.mjs
│   │   ├── sessions/
│   │   │   ├── execution_control.mjs
│   │   │   └── synthesis.mjs
│   │   ├── host.mjs
│   │   ├── operations.mjs
│   │   └── resources.mjs
│   ├── README.md
│   ├── build.mjs
│   ├── check_config.py
│   ├── cli.py
│   ├── cordis.patch.yml
│   ├── package-lock.json
│   ├── package.json
│   └── stage_package.mjs
├── pipelines/
│   ├── benchmark/
│   │   ├── design/
│   │   │   ├── __init__.py
│   │   │   ├── plan_input.py
│   │   │   ├── plan_review.py
│   │   │   ├── plan_versions.py
│   │   │   ├── planner.py
│   │   │   ├── production_allocation.py
│   │   │   └── production_steps.py
│   │   ├── execution/
│   │   │   ├── __init__.py
│   │   │   ├── artifact_graph.py
│   │   │   ├── bindings.py
│   │   │   ├── delivery_contract.py
│   │   │   ├── production_stages.py
│   │   │   ├── repair.py
│   │   │   ├── review_artifacts.py
│   │   │   ├── review_checks.py
│   │   │   ├── review_steps.py
│   │   │   ├── runner.py
│   │   │   └── stage_review.py
│   │   ├── progress/
│   │   │   ├── __init__.py
│   │   │   ├── production_observer.py
│   │   │   └── scene_completion.py
│   │   ├── recipes/
│   │   │   ├── data.capability.json
│   │   │   └── data.package.json
│   │   └── __init__.py
│   ├── reviews/
│   │   ├── __init__.py
│   │   ├── checks.py
│   │   └── service.py
│   ├── scene_production/
│   │   ├── recipes/
│   │   │   ├── asset.prepare.json
│   │   │   ├── scene.capture.json
│   │   │   ├── scene.create.json
│   │   │   ├── scene.extend.json
│   │   │   └── scene.refine.json
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   ├── lifecycle.py
│   │   ├── planning.py
│   │   ├── repair.py
│   │   ├── review.py
│   │   ├── review_checks.py
│   │   ├── scheduler.py
│   │   └── service.py
│   ├── spatial_learning/
│   │   ├── __init__.py
│   │   ├── curriculum.py
│   │   ├── experiments.py
│   │   └── training.py
│   ├── __init__.py
│   └── catalog.py
├── server/
│   ├── deploy/
│   │   └── compose.yml
│   ├── __init__.py
│   ├── cli.py
│   ├── http_api.py
│   ├── migrate_capture.py
│   ├── operations.py
│   ├── operations_runtime.py
│   ├── plan_edit.py
│   ├── plan_edit_cli.py
│   ├── plan_request_cli.py
│   ├── scene_cli.py
│   ├── scene_requests.py
│   ├── spatial_cli.py
│   ├── stage_review_cli.py
│   ├── task_workspace.py
│   ├── training_monitor.py
│   └── workspace_operations.py
├── service_access/
│   ├── __init__.py
│   ├── config_migration.py
│   ├── configuration.py
│   ├── http_tools.py
│   ├── model_inference.py
│   ├── openai_client.py
│   ├── processes.py
│   ├── production_receipts.py
│   ├── progress.py
│   ├── request_context.py
│   ├── runtime_config.py
│   ├── scene_service.py
│   └── tool_names.py
├── storage/
│   ├── scene_jobs/
│   │   ├── __init__.py
│   │   ├── metadata.py
│   │   ├── migrate_capture.py
│   │   ├── store.py
│   │   └── workbench_queries.py
│   ├── __init__.py
│   ├── artifacts.py
│   ├── dataset_partitions.py
│   ├── execution_lock.py
│   ├── file_lock.py
│   ├── files.py
│   ├── native_jobs.py
│   ├── operation_index.py
│   ├── review_progress.py
│   ├── stage_reviews.py
│   ├── training_runs.py
│   └── workspace_jobs.py
├── ui/
│   ├── training_monitor/
│   │   ├── index.html
│   │   ├── monitor.js
│   │   └── styles.css
│   ├── workbench/
│   │   ├── components/
│   │   │   ├── composer.js
│   │   │   ├── composer.module.css
│   │   │   ├── gallery.js
│   │   │   ├── products.js
│   │   │   ├── progress.js
│   │   │   └── workbench.js
│   │   ├── DESIGN.md
│   │   ├── api.js
│   │   ├── api.test.mjs
│   │   ├── composer-store.js
│   │   ├── entry.js
│   │   ├── i18n.js
│   │   ├── primitives.js
│   │   ├── production-input.js
│   │   ├── products-resource.js
│   │   ├── stage_review.test.mjs
│   │   ├── state.js
│   │   └── styles.js
│   └── __init__.py
└── README.md
```
