# Isaac Sim gRPC 服务端接口补充需求

日期：2026-11-24  
协议基线：`isaacsim_rpc.v1`（`simulation.proto`，当前仅 `Health` / `SubmitJob` / `GetJob`）  
消费方：BenchForgeV2（通过 SSH 隧道访问远端 `127.0.0.1:60051`）

## 1. 结论

当前 proto 能提交和查询任务，但**无法把产物取回本地**，也无法安全重试和释放远端磁盘。

按优先级，服务端还需要补：

| 优先级 | 缺口 | 类型 |
|---|---|---|
| P0 | `DownloadArtifact` 流式下载 | 新 RPC |
| P0 | 产物清单字段（`artifact_id` / `relative_path` / `size_bytes` / `sha256` / `media_type`） | 消息字段 |
| P0 | `SubmitJob` 幂等键 `request_id` | 请求字段（签名调整） |
| P0 | `AckJob` 落盘确认与磁盘回收 | 新 RPC |
| P1 | `CancelJob` 释放排队/运行中的 GPU 任务 | 新 RPC |
| P1 | `GetJob` 进度字段（`phase` / `frames_done` / `frames_total`） | 消息字段 |
| P2 | `WatchJob` 服务端推送，替代高频轮询 | 新 RPC |

即：**必需 2 个新接口（DownloadArtifact、AckJob）+ 1 处签名调整 + 清单字段**；建议再加 CancelJob，可选 WatchJob。

## 2. 现状与差距

| BenchForge 需要 | 当前 proto | 差距 |
|---|---|---|
| 提交仿真并等待结果 | `SubmitJob` / `GetJob` | 可用 |
| 断线重试不重复跑任务 | 无 `request_id` | 回包丢失后重试会产生重复仿真 |
| 知道每个产物的大小与校验值 | 只有绝对路径字符串 | 无法校验、无法预估磁盘 |
| 把产物全部拉回本地 | 无下载 RPC | **全量采集无法落地** |
| 下载中断后续传 | 无 | 大帧序列重传成本高 |
| 通知服务端可以清理 | 无 | 远端磁盘会被 2.7 GB/次的帧序列堆满 |
| 用户取消任务 | 无 | GPU 只能等任务自然结束 |
| 展示任务进度 | 只有 state | 长任务只能显示“运行中” |

## 3. 建议的 proto 增量

### 3.1 服务定义

```proto
service SimulationService {
  rpc Health(HealthRequest) returns (HealthResponse);

  // 幂等提交：request_id 相同且参数相同返回原任务。
  rpc SubmitJob(SubmitJobRequest) returns (JobHandle);

  rpc GetJob(GetJobRequest) returns (Job);

  // 新增：流式下载单个产物，支持 offset 续传。
  rpc DownloadArtifact(DownloadArtifactRequest) returns (stream DownloadChunk);

  // 新增：确认客户端已落盘，服务端可回收该任务的产物。
  rpc AckJob(AckJobRequest) returns (AckJobResponse);

  // 建议新增：取消排队中或运行中的任务。
  rpc CancelJob(GetJobRequest) returns (Job);

  // 可选：服务端推送任务状态，客户端仍保留 GetJob 轮询兜底。
  rpc WatchJob(GetJobRequest) returns (stream Job);
}
```

### 3.2 幂等提交

```proto
message SubmitJobRequest {
  // 客户端生成的稳定标识（建议 32 位十六进制）。
  string request_id = 1;

  SimulateRequest simulation = 2;
}
```

语义：

- 同 `request_id` + 同参数：返回已存在的 job，不重复执行；
- 同 `request_id` + 不同参数：返回 `ALREADY_EXISTS`；
- `request_id` 为空：服务端可拒绝（`INVALID_ARGUMENT`），避免客户端误用。

> 这是签名调整。若希望保持 `SubmitJob(SimulateRequest)` 不变，可把 `request_id` 作为 `SimulateRequest` 的新字段，但独立请求体更清晰。

### 3.3 产物清单

```proto
message Artifact {
  string artifact_id = 1;    // 任务内唯一，下载接口的句柄
  string name = 2;           // 展示名，如 render.png
  string relative_path = 3;  // 相对服务端 artifacts 根目录，禁止绝对路径
  uint64 size_bytes = 4;
  string sha256 = 5;         // 小写十六进制
  string media_type = 6;     // image/png、application/x-npy 等
}
```

`Job` 增加：

```proto
repeated Artifact artifacts = 24;   // 字段号顺延，避免与现有 22/23 冲突
string phase = 25;                  // queued | loading_scene | simulating | rendering | writing_artifacts
uint32 frames_done = 26;            // capture 模式下已写出的帧数
uint32 frames_total = 27;           // 计划帧数
```

要求：

- **只返回 `relative_path`**，绝对根目录由服务端配置；客户端不接触绝对路径，天然防目录穿越；
- `size_bytes` 与 `sha256` 必须与最终文件一致，客户端落盘后逐文件校验；
- `frames[]` 中每个 `FrameArtifact` 的图片/深度/语义文件也要有对应的 `Artifact` 条目（`artifact_id` 唯一），不能只给路径。

### 3.4 下载

```proto
message DownloadArtifactRequest {
  string job_id = 1;
  string artifact_id = 2;
  uint64 offset = 3;        // 0 表示从头开始
  uint32 chunk_bytes = 4;   // 建议 1–4 MiB，0 使用服务端默认
}

message DownloadChunk {
  uint64 offset = 1;        // 本块在文件中的起始偏移
  bytes data = 2;
}
```

语义与错误：

| 场景 | 行为 |
|---|---|
| `offset == 0` | 从文件头开始 |
| `offset > size_bytes` | `OUT_OF_RANGE` |
| `offset == size_bytes` | 返回空流并正常结束 |
| job 未成功 | `FAILED_PRECONDITION` |
| `artifact_id` 不属于该 job | `NOT_FOUND` |
| 任务已 Ack 且产物已清理 | `NOT_FOUND`，错误信息注明 `artifacts_released` |
| 客户端断开 | 服务端停止发送，不视为任务失败 |

约束：

- 单块大小上限 4 MiB，建议块 1–4 MiB；
- 服务端应支持同一 job 的 8 条并发下载流；
- 建议限制单 job 并发流数量，超出返回 `RESOURCE_EXHAUSTED`。

### 3.5 落盘确认

```proto
message AckJobRequest {
  string job_id = 1;
}

message AckJobResponse {
  bool released = 1;        // 是否已触发清理
  uint64 freed_bytes = 2;   // 本次释放的字节数
}
```

语义：

- 客户端确认全部产物已校验落盘后调用；
- 服务端可立即删除或标记为可回收；
- 重复 Ack 幂等，返回相同结果；
- 未 Ack 的任务按保留期回收（建议默认 7 天，可配置）。

### 3.6 取消

```proto
// 复用 GetJobRequest，返回取消后的 Job 快照。
rpc CancelJob(GetJobRequest) returns (Job);
```

语义：

- 排队中任务：直接取消，state 置 `interrupted`；
- 运行中任务：尽力中断，返回实际 state；
- 已终态任务：幂等返回当前 Job；
- 取消后仍需允许下载已写出的产物（若服务端支持部分产物），否则明确返回无产物。

### 3.7 健康检查补充

`HealthResponse` 已有 `queue_depth` / `queue_capacity`，建议补充：

```proto
string api_version = 21;          // 契约版本，便于客户端断言
string artifacts_root = 22;       // 仅用于诊断，不参与路径拼接
uint64 artifacts_free_bytes = 23; // 剩余磁盘，供提交前判断
uint32 retention_seconds = 24;    // 产物保留期
```

## 4. 行为约束

### 4.1 任务状态

| state | 含义 |
|---|---|
| `queued` | 已受理，排队中 |
| `running` | 仿真/渲染执行中 |
| `succeeded` | 全部产物写完，可下载 |
| `failed` | 执行失败，`error` 非空，无可用产物 |
| `interrupted` | 被取消或服务重启中断 |

要求：

- `succeeded` 之前 `artifacts` 可以为空，但一旦非空必须完整；
- `error` 用稳定前缀区分原因，例如 `invalid_scene: ...`、`render_failed: ...`、`timeout: ...`、`internal: ...`；
- 服务重启后，运行中的任务应转为 `interrupted`，不要让客户端无限轮询。

### 4.2 输入约束（与文档一致）

| 字段 | 约束 |
|---|---|
| `steps` | 1–1200，缺省 240 |
| `dt` | 0.0001–0.02，缺省 1/120 |
| `gravity` | 0–100 |
| `render.size` | 64–1024 |
| `render.spp` | 1–256 |
| `capture.frames` | 1–1200 项，自动去重排序 |
| `body.id` | 唯一；作为语义标签，禁止重复 |
| `body.shape` | `cube` / `sphere` / `mesh` |
| `body.asset` | 仅 mesh，服务端可读目录，内含 `mesh.npz` |

越界时返回 `INVALID_ARGUMENT`，错误信息包含字段名与实际值。

### 4.3 产物保留

- 未 Ack：按 `retention_seconds` 保留，过期清理；
- 已 Ack：允许立即清理；
- 清理后 `GetJob` 仍可返回状态与 manifest，但下载返回 `NOT_FOUND`；
- 建议服务端提供磁盘水位保护：低于阈值时拒绝新任务并返回 `RESOURCE_EXHAUSTED`。

## 5. 推荐交互时序

```text
BenchForge                                   SimulationService
    │ SubmitJob(request_id, simulation) ───────────> │
    │ <──────────────── JobHandle(job_id)            │ 幂等
    │                                                │
    │ GetJob(job_id) ── 0.5s → 2s → 5s 自适应轮询 ──> │
    │ <──────── Job(state, phase, progress, artifacts)
    │                                                │
    │ DownloadArtifact(job_id, artifact_id, offset) >│
    │ <────────────── stream DownloadChunk ───────── │ 并发 4–8，可续传
    │                                                │
    │ 校验 size + sha256，原子落盘                    │
    │ AckJob(job_id) ───────────────────────────────> │ 允许清理
```

轮询建议：0–10 秒每 0.5 秒，10–60 秒每 2 秒，60 秒后每 5–10 秒；接入 `WatchJob` 后可降为低频兜底。

## 6. 服务端验收清单

- [ ] 同 `request_id` 重复提交只产生一个 job；参数不同返回 `ALREADY_EXISTS`
- [ ] `GetJob` 返回的每个 `artifact` 的 `size_bytes`、`sha256` 与文件实际一致
- [ ] `DownloadArtifact` 从任意 `offset` 续传，拼接后 sha256 一致
- [ ] 8 条并发下载流稳定，不出现截断或错序
- [ ] 下载中客户端断开，服务端不把任务标记为失败
- [ ] `AckJob` 后磁盘可回收，重复 Ack 幂等
- [ ] 未 Ack 的产物按保留期清理，清理后下载返回 `NOT_FOUND`
- [ ] `CancelJob` 能取消排队任务，运行中任务返回实际状态
- [ ] 服务重启后运行中任务变为 `interrupted`
- [ ] `Health` 在初始化阶段返回 `status=initializing`，就绪后返回 `ready`
- [ ] 越界参数返回 `INVALID_ARGUMENT`，错误信息含字段名
- [ ] SSH 隧道空闲 60 秒后，`GetJob` 与下载仍可用（服务端不主动掐长连接）

## 7. BenchForge 侧对应改造（服务端就绪后执行）

1. 用新版 proto 重新生成 stub，替换现有 `a100sim.v1` 客户端；
2. 新增 `SimulationClient`：`health` / `submit(request_id)` / `get` / `download(offset)` / `ack` / `cancel`；
3. `capture_stage` 改为：SceneProgram → `SimulateRequest`（bodies / render / capture）→ 轮询 → 全量下载 → 校验 → `evidence.json` → `AckJob`；
4. 多相机映射为多次提交（每次一个 `RenderSpec` 方位角），产物按 `view_N` 落盘；
5. `mesh` / `native` 等当前协议无法表达的几何，在提交前报 `UnsupportedScene`，不静默降级；
6. 本地 manifest 记录 `job_id`、每个文件的 `sha256`、`size_bytes`、`fetched_at`，作为不可变回执。
