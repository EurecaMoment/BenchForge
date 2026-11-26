# Isaac Sim SimulationService v2 集成

BenchForge 调用远端 `isaacsim_rpc.v1.SimulationService` gRPC 服务完成场景采集。
服务通过 SSH 隧道暴露到本地 `127.0.0.1:60051`。采集全程无本地 Kit/Omniverse runtime。

## 协议契约（gRPC v2）

```
service SimulationService {
  rpc Health(HealthRequest) returns (HealthResponse);
  rpc SubmitJob(SimulateRequest) returns (Job);          // request_id 幂等
  rpc GetJob(GetJobRequest) returns (Job);                // 轮询 Job.phase
  rpc WatchJob(GetJobRequest) returns (stream Job);       // 流式状态更新
  rpc DownloadArtifact(DownloadArtifactRequest)
      returns (stream ArtifactChunk);                     // 逐 artifact 流式拉取
  rpc AckJob(AckJobRequest) returns (AckJobResponse);     // 释放远端磁盘
  rpc CancelJob(CancelJobRequest) returns (CancelJobResponse);
}
```

Job phase 枚举：`QUEUED → RUNNING → FRAMES_DONE → SIMULATION_DONE → RENDERING_DONE → COMPLETED | FAILED | CANCELED`。
Job 携带 `frames_done` / `frames_total` 进度字段，GetJob 自适应轮询（初始 1s，最大 30s，按 phase 转移重置）。

## 模块

| 模块 | 职责 |
| --- | --- |
| `grpc/simulation.proto` + 生成桩 | v2 契约，SimulationService + Job + Artifact |
| `grpc/transport.py` | `SimulationClient`：keepalive/TLS 可选、32MiB 收包、自适应轮询、流式下载逐 artifact + 偏移校验 + sha256 |
| `grpc/tasks.py` | `DurableJobs(client, directory, namespace)`：幂等 submit（request_id）、wait（状态回调持久化）、download |
| `scene_mapping.py` | `simulation_plan(program, settings, scope)`：刚体映射、支撑拓扑、相机方位角/距离、自适应采样、mesh_assets 绑定 |
| `scene_requirements.py` | 白名单校验：box/sphere/container/composite 支持；cylinder/native/generated/内建资产 → UnsupportedScene；mesh 需 `mesh_assets` 配置 |
| `capture_stage.py` | `execute_capture()`：Health → 每相机 DurableJobs submit/wait/download → 逐帧 evidence（规范名 view_{i} / 过程名 process_view_{i}_step_XXXXXX） → entities / physics_summary → evidence/report/manifest → store.publish_capture → AckJob（尽力） |
| `capture_contract.py` | 静态契约校验：`capture_service_issues()` / `health_issues()` / `inspect_live_capture_service()` |
| `evidence.py` | `frame_evidence()` 逐帧落盘 RGB+depth(.npy)+semantic(.npy)+instance(.npy)；`entities_from_job()` 从 BodyState 计算最终 pose；`physics_summary()` 从 velocity 计算 |
| `capture_scope.py` | 允许 frames/every 字段；`export_scene` 默认 False |
| `capture_contract.py` | 静态契约 + live Health 校验 |
| `tool_api.py` | DSH `benchforge_isaac_sim` 动作：guide / health / submit / simulation / status / wait / cancel / download / ack |
| `guide.py` | 返回静态契约说明（6 个操作），不再读在线 catalog |
| `recovery.py` | state 比较小写兼容（'queued'/'running'/'failed'/'interrupted'） |

## 配置

`config/benchforge.local.json` → `providers.IsaacSim4A100`：

```json
{
  "grpc": {"host": "127.0.0.1", "port": 60051},
  "simulation": {"steps": 180, "dt": 0.0166666667, "gravity": 9.81, "ground": true,
                 "capture_target_frames": 24},
  "render": {"fov_degrees": 50, "seed": null},
  "artifacts": {"chunk_bytes": 2097152, "concurrency": 4},
  "mesh_assets": {}
}
```

## 采集流程

```
SceneProgram
  → simulation_plan(program, settings, scope)    # 刚体/支撑/相机/采样
  → simulation_request(plan, view)               # 单相机 SimulateRequest
  → Health() probe                                # api_version / retention / free_disk
  → DurableJobs.submit(request_id 幂等)          # SubmitJob
  → DurableJobs.wait(自适应轮询)                 # GetJob / WatchJob
  → DurableJobs.download(job, directory)          # 逐 artifact 流式 + 偏移校验 + sha256
  → frame_evidence(每帧)                          # 规范名 + 过程名
  → entities_from_job / physics_summary
  → store.publish_capture                          # evidence.json / report.json / manifest.json
  → AckJob (尽力)                                  # 释放远端磁盘
```

## 多帧采样

默认 `capture_target_frames=24`，采集间隔 `every = max(1, ceil(steps / target_frames))`：
- 最终帧强制包含，命名 `view_{i}.*`（规范名）
- 过程帧命名 `process_view_{i}_step_{step:06d}.*`（前缀刻意不用 `view_` 避免复核提示词爆炸）

复核抽样：每相机保留最终帧 + 最多 3 张均匀抽样的过程帧（见 `src/pipelines/scene_production/review.py::_review_frames()`）。

## 引用约定

- `providers.IsaacSim4A100`：配置节
- `isaacsim_rpc.v1.SimulationService`：协议全名，所有 event / receipt / report 里用这个值
- `capabilities.isaac_sim`：Python 模块路径
- `benchforge_isaac_sim`：DSH 工具名
