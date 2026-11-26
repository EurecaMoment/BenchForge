# Isaac Sim gRPC v2 适配 Spec

日期：2026-11-24
状态：待批准
关联需求：[IsaacSim-gRPC-服务端接口补充需求](../IsaacSim-gRPC-服务端接口补充需求.md)

## 1. 目标与边界

把 BenchForgeV2 的 Isaac Sim 采集链路从 `a100sim.v1.Simulator`（capabilities / upload /
extension / download）切换到 `isaacsim_rpc.v1.SimulationService`（Submit / Get / Download /
Ack / Cancel）。仿真与渲染全部在远端完成，产物全量拉回本地后再发布。

范围内：
- 新增 v2 proto 与 stub；删除旧 `simulator.proto` 与 `simulator_pb2*`。
- 新增 `SimulationClient` 与持久作业/下载回执。
- 重写采集编排：SceneProgram → `SimulateRequest` → 轮询 → 下载校验 → evidence/report → Ack。
- 新工具的 `health/submit/status/wait/cancel/download` 动作；移除 upload/catalog/invoke 等旧动作。
- 配置改为 SSH 隧道端口 60051，新增 simulation/render/artifacts 配置。
- 相关测试按新契约重写，使用本地 mock gRPC server。

### 1.1 现状确认

旧 `a100sim` 适配器确实只取最终帧：先 `physics.step` 沉降 180 步，再对每台相机
`scene.capture` 一次，因此 evidence 每相机只有一张图。

下游数据层支持多帧：`evidence['frames']` 按 `frame_id`/`step_id`/`timestamp_sim`
逐帧描述，`dataset_authoring/scene_capture/products.py` 会遍历全部帧生成题目；
`acceptance.py` 只强制要求每相机存在 `view_{i}.png`，额外过程帧不影响验收。
但 `pipelines/scene_production/review.py` 会用 `capture.glob('view_*.png')` 收集复核图，
若过程帧沿用 `view_*` 命名会把大量图片塞进模型提示词。

结论：新适配器应采集大量过程帧供数据生产使用，同时把“数据帧”和“复核帧”分开命名、
在复核阶段抽样，避免上下文爆炸。

明确不做：
- 不改远端服务端代码（不在本机）。
- `native` / `generated` / 目录内建原生资产：提交前报 `UnsupportedScene`，不静默降级。
- `mesh`：仅当配置了 `providers.IsaacSim4A100.mesh_assets`（本地 `asset_id` → 服务端
  含 `mesh.npz` 的目录绝对路径）时才提交；未配置则报 `UnsupportedScene`。
- `cylinder`：`BodySpec` 只有 `cube` / `sphere` / `mesh`，无圆柱表示，提交前报
  `UnsupportedScene`。
- 不支持场景导出（usdc/usdz）；显式 `export_scene: true` 报 `UnsupportedScene`。

## 2. 协议决策

### 2.1 proto

新增 `src/capabilities/isaac_sim/grpc/simulation.proto`，`package isaacsim_rpc.v1;`，
`service SimulationService` 包含：

- `Health`
- `SubmitJob(SubmitJobRequest)`
- `GetJob(GetJobRequest)`
- `DownloadArtifact(DownloadArtifactRequest) returns (stream DownloadChunk)`
- `AckJob(AckJobRequest)`
- `CancelJob(GetJobRequest)`
- `WatchJob(GetJobRequest) returns (stream Job)`（可选，不依赖）

字段以用户提供的 `simulation.proto` 原件为基础，再追加需求文档第 3 节：
`SubmitJobRequest{request_id, simulation}`、`Artifact{artifact_id,name,relative_path,
size_bytes,sha256,media_type}`、`Job{artifacts,phase,frames_done,frames_total}`、
`HealthResponse{api_version,...}`。

生成：
```bash
.venv/bin/python -m grpc_tools.protoc -I src/capabilities/isaac_sim/grpc \
  --python_out=src/capabilities/isaac_sim/grpc \
  --grpc_python_out=src/capabilities/isaac_sim/grpc \
  src/capabilities/isaac_sim/grpc/simulation.proto
```
生成后修正包内导入为 `from capabilities.isaac_sim.grpc import simulation_pb2 as ...`。

### 2.2 状态与轮询

- 终态：`succeeded` / `failed` / `interrupted`（兼容大写枚举字符串，统一小写比较）。
- `failed` 抛 `RemoteJobFailed`，保留 Job 文档。
- 轮询节奏：0–10s 每 0.5s；10–60s 每 2s；>60s 每 5s。
- `stop.canceled` 时调用 `CancelJob` 后抛 `RunCancelled`；仅 `stop` 时保留回执并抛
  `RunCancelled`，不取消远端。
- 超过 `capture_timeout_seconds` 抛 `TimeoutError`，保留 receipt。

### 2.3 下载与校验

- 逐 Artifact 流式下载到 `.incoming`，`offset == size_bytes` 视为已完整。
- 每块校验 `chunk.offset` 连续、不超过 `size_bytes`；结束后校验长度、`sha256`。
- 校验通过后原子 rename；失败保留 `.partial`。
- 下载清单写 `files.json`（artifact_id/name/relative_path/size_bytes/sha256/media_type）。
- `AckJob` 在本地发布成功后调用；Ack 失败只记录进度，不回滚已发布采集。

## 3. SceneProgram → SimulateRequest 映射

新模块 `src/capabilities/isaac_sim/scene_mapping.py`。

入口：
```python
def simulation_plan(program, settings, scope):
    """返回 {'bodies': [...], 'entities': {object_id: {...}}, 'views': [{index,camera,...}]}"""
```

### 3.1 刚体

- 复用 `preview_scene_geometry(program)` 得到 `initial_center_m` 与 `actual_size_m`。
- `box` → `BodySpec(shape='cube', size=size, position=center)`。
- `sphere` → 要求 `abs(dx-dy),abs(dy-dz) <= 1e-6`，`radius=size[0]/2`；否则 UnsupportedScene。
- `container` → 按现有 authoring 的 5 块盒壁拆分，每块一个 BodySpec，id = `<obj>__part<k>`。
- `composite` → `parts` 中每块 `box`/`sphere` 一个 BodySpec，id = `<obj>__part<k>`；
  `cylinder` 或其它 shape → UnsupportedScene。
- `cylinder` → UnsupportedScene（协议无圆柱表示）。
- `mesh` → 仅当 `settings.mesh_assets[asset_id]` 存在时提交：
  `BodySpec(shape='mesh', asset=<服务端绝对路径>)`，不写 size/radius；
  未配置该绑定 → UnsupportedScene，并在 issue 中说明需要预置服务端资产。
- `generated` / `native` / `kind in ASSETS` / 其它 → UnsupportedScene。
- 位姿：四元数仅绕 Z 轴 `yaw_deg`；`position` 为世界坐标。部件 world 位置 =
  `center + Rz(yaw) @ offset`。
- 颜色取对象 `color` 或材质 `base_color`；质量：`dynamic=true` 写 `mass`，
  `dynamic=false` 省略 `mass`（沿用上游默认静态语义，并在 evidence 标注
  `static_body_semantics='upstream_default'`）。
- `gravity` / `ground` / `steps` / `dt` 来自 `settings.simulation`。

### 3.2 相机

每个选中的 view 生成一次 SubmitJob：
- `target = camera.target`
- `delta = position - target`；`distance = |delta|`
- `elevation = degrees(asin(dz/distance))`
- `azimuth = degrees(atan2(dy, dx))`
- `RenderSpec(azimuth,elevation,distance,target,size,spp,seed)`。
- FOV 无法由协议表达：使用 `settings.render.fov_degrees`（默认 50），evidence 标
  `fov_source='configured_default'`、`camera_calibration='reconstructed_from_program_pose'`。

### 3.3 Capture（自适应过程帧）

为什么不是每步都采：60Hz 下相邻步只差约 16.7ms，画面和状态几乎重复，既浪费
GPU/存储，也不增加轨迹信息。合理做法是按总步数选间隔，使每相机帧数有上限、
相邻样本之间有可辨位移。

默认策略（目标帧数驱动）：
- `target = settings.simulation.capture_target_frames`，默认 `24`。
- 未显式配置 `capture_every_steps` 时：`every = max(1, ceil(steps / target))`。
  180 步、target=24 → `every=8`，约 23 帧/相机（含最终帧）。
- `capture_every_steps` 为非空正整数时直接使用该固定间隔。
- 最终帧始终包含：`CaptureSpec(every=<effective>, frames=[steps])`，服务端取并集。
- `capture_options` 覆盖优先级最高：
  - `frames`：显式帧号列表，1..steps，去重排序，最多 1200 项，只采这些帧；
  - `every`：正整数，1..steps，覆盖自动/配置间隔；
  - 两者同时给出时都透传。
- 每个 job 的帧数上限 1200（协议上限）；超出时报 `UnsupportedScene`。
- 每次采集 = N 个 job（N = 选中相机数），每个 job 使用同一 bodies/steps/dt，
  request_id = `token + ':view-<index>'`。
- `steps` 默认 180，`dt` 默认 1/60；`dt` 范围 0.0001..0.02。

帧命名（数据帧与复核帧分离）：
- 最终帧（`step == steps`）：`frame_id = view_{index}`，规范文件
  `view_{index}.png/.json/_depth.npy/_semantic.npy/_instance.npy`。
  这是复核页面与验收强制要求的每相机一张图。
- 过程帧：`frame_id = process_view_{index}_step_{step:06d}`，文件同后缀。
  前缀刻意不用 `view_`，避免被 `capture.glob('view_*.png')` 收入复核提示词。
- 服务端只给 `semantic_ids` 时，同一数组同时写 `_semantic.npy` 与 `_instance.npy`，
  evidence 标注 `instance_source='semantic_ids'`。
- `report.frames` = 全部帧数（相机×帧），`report.views` = 相机数，
  `report.process_frames` = 过程帧数；`report.capture_plan` 记录
  `{steps, target_frames, every, frames}`，便于复现采样密度。
- `report.capture_scope.view_indices` 保持不变。

## 4. 文件级改动

### 4.1 gRPC 层

| 文件 | 动作 | 内容 |
|---|---|---|
| `grpc/simulation.proto` | 新增 | v2 契约 |
| `grpc/simulation_pb2.py` / `_grpc.py` | 生成 | stub |
| `grpc/simulator.proto` / `simulator_pb2*` / `protocol.json` | 删除 | 旧 a100 契约 |
| `grpc/transport.py` | 重写 | `SimulationClient`：`health/submit/get/download/ack/cancel/watch/close`；保留 `document()`；keepalive、TLS 可选、超时、32MiB 收包；不再有 `upload/call('GetCapabilities')` |
| `grpc/tasks.py` | 重写 | `DurableJobs(client, directory, namespace, *, stop, on_status)`：`submit(key, simulation)`、`wait(key, job)`、`download(key, job)`；`request_id()` 保留；回执目录不变 |
| `grpc/receipts.py` | 保留 | 跨进程锁 |

`DurableJobs.submit` 冻结 `{protocol, endpoint, request_id, simulation}` 到 `request.json`；
已有 `receipt.json` 时 `GetJob` 复用；一次 Submit 的瞬时错误按 `UNAVAILABLE /
DEADLINE_EXCEEDED / RESOURCE_EXHAUSTED` 重试。

### 4.2 采集层

| 文件 | 动作 | 内容 |
|---|---|---|
| `scene_mapping.py` | 新增 | 第 3 节映射；`UnsupportedScene` 带结构化 issues |
| `capture_contract.py` | 重写 | 去掉 capabilities/schema 对账；`capture_service_issues(program, settings, scope)` 做静态契约校验；`require_capture_service(...)`；`capture_service_client(config)` 返回 `SimulationClient`；`inspect_live_capture_service(program, settings, *, scope=None)` 调 Health 后做静态校验，`status != ready` 报 `service_unavailable`，`api_version` 非空且不匹配报 `service_contract_mismatch` |
| `scene_requirements.py` | 修改 | `capture_requirement_issues` 改为新几何白名单（box/sphere/composite/container；parts 仅 box/sphere）；移除 `asset_sources`；`native_reference_issues` 改为“native 资产不受新契约支持”；保留 interactions 拒绝 |
| `capture_stage.py` | 重写 | 流程见第 5 节 |
| `src/pipelines/scene_production/review.py` | 修改 | `camera_receipts` 改为复核抽样：每相机保留最终帧，并从过程帧中均匀抽最多 3 帧；数据帧保留在 evidence/磁盘，不删除。避免 `view_*.png` 提示词爆炸 |
| `evidence.py` | 重写 | 保留 `physics_summary`；新增 `frame_evidence(frame, artifacts, camera_index, camera, program, output, settings, job)`（逐帧、规范名/过程名）；新增 `entities_from_job(program, job, plan)`；删除 `resolve_artifact`/`measured_entities`/旧 `save_frame` |
| `authoring.py` / `usd_materials.py` / `mesh_materials.py` / `native_geometry.py` / `upload_access.py` / `resource_access.py` | 删除 | 新契约无 USD 编译、上传、原生会话 |
| `guide.py` | 重写 | 返回静态契约说明（health/submit/status/wait/cancel/download），不再依赖 live catalog |
| `recovery.py` | 修改 | 回执 glob 不变；`state` 兼容小写；`protocol migration` 文案改为 v2 |

### 4.3 工具与存储

| 文件 | 动作 | 内容 |
|---|---|---|
| `tool_api.py` | 重写 | 动作：`guide/health/submit/status/wait/cancel/download`。`submit` 接受 `simulation`（`SimulateRequest` JSON）；保留 `NativeJobs` 会话/端点归属校验；下载成功后返回 `files/artifacts/receipt_path` |
| `storage/scene_jobs/store.py` | 修改 | `publish_capture` 事件 protocol 改为 `isaacsim_rpc.v1.SimulationService` |
| `capabilities/capture/observation.py` | 修改 | 容量原因 protocol 改为 v2 |
| `storage/scene_jobs/migrate_capture.py` | 修改 | 迁移提示文案改为 v2 |
| `service_access/configuration.py` | 修改 | `validate_providers` 允许 `simulation/render/artifacts/mesh_assets`；删除 `asset_sources` 校验；校验 ranges；`mesh_assets` 为 `{asset_id: server_path}` |
| `config/benchforge.local.json` / `.example.json` | 修改 | port 60051；新增 simulation/render/artifacts；用 `mesh_assets` 替代 `asset_sources` |
| `capabilities/capture/capture_scope.py` | 修改 | 允许 `frames`/`every`；`export_scene` 默认改为 `False`（v2 无导出产物）；显式 true 由契约层拒绝 |

配置示例：

```json
{
  "grpc": {"host": "127.0.0.1", "port": 60051, "timeout_seconds": 60},
  "capture_timeout_seconds": 1800,
  "max_concurrent_captures": 4,
  "image_size": 768,
  "samples_per_pixel": 32,
  "simulation": {
    "steps": 180,
    "dt": 0.016666666666666666,
    "gravity": 9.81,
    "ground": true,
    "capture_every_steps": null,
    "capture_target_frames": 24
  },
  "render": {"fov_degrees": 50.0, "seed": 0},
  "artifacts": {"chunk_bytes": 2097152, "concurrency": 4},
  "mesh_assets": {}
}
```

校验范围：`steps` 1..1200；`dt` 0.0001..0.02；`gravity` 0..100；
`capture_every_steps` 为 null 或 1..1200；`capture_target_frames` 1..1200；
`image_size` 64..1024；`samples_per_pixel` 1..256；`fov_degrees` 1..179；
`chunk_bytes` 1..4194304。

容量提示：默认 target=24 时约 24 帧/相机；768² RGB+depth+semantic 估算
约 80–120 MB/相机，4 相机约 0.3–0.5 GB/场景。需要更密轨迹时调大
`capture_target_frames` 或显式设置 `every`/`frames`。

### 4.4 DSH 工具契约

| 文件 | 动作 |
|---|---|
| `src/dsh/tools/benchmark/contracts.mjs` | action enum 改为 `guide/health/submit/status/wait/cancel/download`；删除 upload/asset 字段，新增 `simulation` |
| `src/dsh/production/review_policy.mjs` | `inspections` 改为新动作集合 |
| `src/dsh/skills/isaac-sim/references/tool-api.md` | 按新动作重写 |
| `src/dsh/skills/isaac-sim/SKILL.md`、`presets/preset_definition.py`、`cordis.patch.yml` | 文案改为 v2，不再提 catalog/upload |
| `src/dsh/workbench/artifacts/artifacts.test.mjs` | 下载测试改用新 job 结构 |

## 5. `execute_capture` 流程

```
load program / settings / scope
validate_scene_requirements(program, settings)
plan = simulation_plan(program, settings, scope)          # 失败则 UnsupportedScene，零 RPC
token = request_id(str(directory), f'capture-{generation}')
收尾：report.json 已存在且 token 一致 → 直接 READY
冻结 capture.json {program, capture_scope, settings, protocol, capture_token, plan摘要}
health()（连接/就绪检查）
incoming = attempt/capture（先清空重建）
for view in plan.views:
    key = f'view-{index}'
    job = jobs.submit(key, request_for(view))          # 含 every / frames
    job = jobs.wait(key, job)
    files = jobs.download(key, job)
    for frame in job.artifacts.frames:                  # 多帧：逐帧转证据
        frames.append(frame_evidence(...))
    job_receipts.append({job_id, request_id, camera_index, artifacts, fetched_at})
write evidence.json / report.json / program.json / manifest.json
check_cancelled(stop, 'capture publication')
store.publish_capture(task_id, token, incoming)
for job in job_receipts: 尽力 AckJob，失败仅 record_progress
```

`report.json` 保持现有消费方字段：`token/status='captured'/renderable/frames/views/
physics/errors/render_settings/interaction/render_qa/capture_scope`，新增 `protocol/
job_ids/manifest`。`evidence.json` 保持 `benchforge.evidence/v1`，`origin='isaacsim_rpc'`，
`protocol='isaacsim_rpc.v1.SimulationService'`；`frames` 逐帧记录相机、步号、
`image`（相对名）及质量。`report.frames` 是全部帧数，`report.views` 是相机数。

## 6. Artifact 解析规则

`Artifact` 只有 `relative_path`（禁止绝对路径）。Frame 引用可能是绝对路径或
`artifact://<id>`。解析函数按以下顺序唯一定位，歧义即报错：

1. `artifact://<id>` → `artifact_id` 精确匹配；
2. 绝对路径规范化后以 `'/' + relative_path` 或 `'/' + name` 结尾；
3. `name` 精确匹配。

## 7. 测试改造

新增/重写：

| 文件 | 内容 |
|---|---|
| `tests/test_isaac_sim_grpc.py` | mock `SimulationService`：幂等提交、丢包重试、自适应轮询、下载 offset 续传、sha 校验、Ack、Cancel |
| `tests/test_simulation_request.py` | box/sphere/container/composite 映射；yaw 旋转；mesh 绑定成功/缺失拒绝；cylinder/native/generated 拒绝；相机 azimuth/elevation/distance；自适应 `ceil(steps/target)` 间隔与 `frames`/`every` 覆盖 |
| `tests/test_capture_service_contract.py` | 静态契约；Health 非 ready；UnsupportedScene 时零 Submit/Download |
| `tests/test_isaac_sim_evidence.py` | artifact→多帧 evidence；最终帧规范名与过程帧命名；语义标签两种格式；physics_summary |
| `tests/test_native_scene_reference.py` | native 在准入阶段被拒绝，不再编译 USD |
| `tests/test_asset_capture_contract.py` | mesh/native 拒绝与错误结构 |
| `tests/test_capture_preflight.py` | 去掉 asset_sources；mesh 预检失败 |
| `tests/test_capture_service_recovery.py` | 保留 `inspect_live_capture_service` mock；v2 文案 |
| `tests/test_capture_migration.py` | protocol 文案 |
| `tests/test_operation_outputs.py` | 下载返回结构改用新工具动作 |

删除：`tests/test_isaac_upload_access.py`、`tests/test_isaac_upload_inputs.py`、
`tests/test_isaac_resource_access.py`、`tests/test_isaac_sim_guide.py`。

## 8. 验证

```bash
cd /home/maqiang/zhouwei/envs/BenchForgeV2
.venv/bin/python -m compileall -q src
.venv/bin/python -m unittest -v \
  tests.test_isaac_sim_grpc \
  tests.test_simulation_request \
  tests.test_capture_service_contract \
  tests.test_isaac_sim_evidence \
  tests.test_native_scene_reference \
  tests.test_asset_capture_contract \
  tests.test_capture_preflight \
  tests.test_capture_service_recovery \
  tests.test_operation_outputs
```

验收：
- 无旧 `a100sim` / `simulator_pb2` 引用（`grep` 为空）。
- 不支持场景在首个 gRPC 调用前失败。
- mock 服务下完整采集产出 `report.json/evidence.json/manifest.json` 并调用一次 Ack。
- 默认配置下每相机约 `capture_target_frames` 帧（180 步/target=24 → every=8）；
  `view_{i}.png` 存在，`process_view_{i}_step_*.png` 存在，且 `evidence.frames`
  覆盖过程帧与最终帧。
- `report.capture_plan.every` 与帧清单一致；显式 `capture_options.every`/`frames`
  能覆盖自动间隔。
- 复核提示词只包含每相机最终帧与最多 3 张过程帧（数据文件不受影响）。
- 下载中断续传后 sha256 与清单一致。

## 9. 风险与显式取舍

- 上游默认值未知：静态刚体通过“省略 mass”表达；evidence 标注来源，不声称已校准。
- `mesh` 依赖运维预先把 `mesh.npz` 目录放到服务端；BenchForge 不再提供上传接口。
  未配置 `mesh_assets` 时明确拒绝，不做路径猜测。
- 相机内参无法表达：FOV 用配置默认，evidence 标注重建来源。
- `semantic_labels` JSON 结构未在协议中固定：解析器兼容旧 `classes/instances` 与
  `id -> label` 两种形式；无法唯一匹配时失败而不是猜测。
- `WatchJob` 为可选；实现优先轮询，Watch 仅作为可选加速，不阻塞验收。
