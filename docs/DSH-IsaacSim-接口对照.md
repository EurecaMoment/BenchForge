# DSH 与 Isaac Sim 接口对照

状态（2026-11-19）：本文包含本轮源码中的会话隔离与接口适配。正式 gRPC `50051` 尚未切换到全部候选改动；工具注册、阶段测试和本文更新都不证明已经上线。

BenchForge 的 DSH 预设通过 `benchforge_isaac_sim` 使用 `a100sim.v1.Simulator` gRPC 服务。常规场景生产则由 `benchforge_capture` 等工具提交给 BenchForge 后端，再由后端调用同一 gRPC 服务。**运行时以服务返回的能力、参数定义和实际作业结果为准，不读取 Git 提交号判断功能。** Git 子模块信息只用于维护者部署与追溯源码。

本说明区分三件事：工具已经注册、服务声明可用、真实任务验收通过。前两项都不能单独证明第三项。协议中的 `api_version`、`simulator_version` 可以帮助诊断，但不能替代具体操作及参数检查。

## 使用入口与责任

| 入口 | 用途 | 执行责任 |
| --- | --- | --- |
| `benchforge_isaac_sim` | 在线能力/目录查询、资产上传、原生任务提交、观察、取消与下载 | DSH 官方工具执行器 → BenchForge Python 适配器 → gRPC |
| `benchforge_capture` | 保存完整 SceneProgram 后采集场景，生成真实图片与物理回执 | BenchForge 后端调度、准入、原生采集与质量验收 |
| `benchforge_refine` / `benchforge_extend` | 修改或扩展场景，保留来源版本及验收规则 | 新的后端生产任务，经同一采集阶段执行 |
| `benchforge_retry` | 恢复失败或中断的阶段 | 复用冻结请求、原生作业回执和部分下载；终态失败才创建新尝试 |
| `benchforge_scene_status` / `benchforge_wait` | 查询或订阅 BenchForge 生产进度 | Host 的采集续行服务，不等同于原生 `GetJob` |
| 右侧评测工作台 | 展示计划、场景、采集、题目和验收产物 | 消费保存的回执与投影，不直接调用 gRPC，也不推断任务通过 |

本轮 BenchForge 源码在 DSH 原生“工作区内修改”权限上增加显式读范围：本会话工作区、必要运行时、授权 Skill 和资产版本；其他会话、第三方源码、权重与凭据目录默认不开放。用户仍可显式选择“完全权限”，工作台不自动提权；完全权限下不能承诺任意脚本保持相同主机边界。仿真调用使用已配置的服务，不要求模型进入服务源码目录、启动服务、安装依赖或查看 Git 状态。实现与未完成验收见 [工作区隔离与数据访问](BenchForge-工作区隔离与数据访问设计.md)。

模型的正式文档入口是随预设提供的 [isaac-sim Skill](../src/dsh/skills/isaac-sim/SKILL.md)，通过 DSH 原生 `skill({"name":"isaac-sim"})` 按需加载。动作、参数、回执生命周期与示例位于同包的 [references/tool-api.md](../src/dsh/skills/isaac-sim/references/tool-api.md)，场景操作与会话边界位于 [references/scene-operations.md](../src/dsh/skills/isaac-sim/references/scene-operations.md)。模型使用这些文档和实时 `guide`，不需要读取第三方源码。本文件后半部分的源码定位与部署说明供维护者查证，不是数据生产模型的排障流程。

## `benchforge_isaac_sim` 动作与 gRPC 方法

`workspace` 是 Host 注入并核验的当前 DSH 会话工作区，不再要求模型填写。下面示例使用模型可见参数；仿真器部署目录不属于任务输入。

| `action` | gRPC 方法 | 主要输入 | 结果与注意事项 |
| --- | --- | --- | --- |
| `guide` | `GetCapabilities` + `GetApiCatalog` | 可选 `operation` 或 `feature_id` | 模型可直接读取的使用说明及在线操作摘要；指定操作/功能时返回完整在线字段，文档不代替真实作业验收 |
| `capabilities` | `GetCapabilities` | 无额外输入 | 在线操作名、可用性、原因及参数定义；同时保存 `capabilities.json` |
| `catalog` | `GetApiCatalog` | 可选 `feature_id` | 产品能力目录、`support`、`availability`、`request_schema`、`fixed_arguments` 和实现边界 |
| `assets` | `ListAssets` | 无额外输入 | 本会话已登记授权的上传资产，不返回任意服务端文件浏览能力 |
| `upload` | `UploadAsset` | 工作区中的 `source_path`；可选 `asset_id`、`relative_path` | 返回 `asset_id` / `relative_path`；后续请求引用这对标识，不传服务端绝对路径 |
| `submit` | `SubmitTask` | `request`、可选稳定 `request_id` | `request` 是原生任务消息，不在其中重复嵌入 `request_id`；返回受理作业，不代表完成 |
| `invoke` | `InvokeFeature` | `feature_id`、`request`、可选稳定 `request_id` | `request` 直接放该功能的参数；不要套 `extension` 外壳。功能 ID 从当前目录读取 |
| `status` | `GetJob` | 同一会话持有的 `job_id` | 读取原生状态、失败原因、结果和产物清单 |
| `wait` | `GetJob` + `WatchJob` | `job_id`、`timeout_seconds` | 每次观察最长 60 秒。超时保留远端任务，不重新提交，也不等于失败或取消 |
| `cancel` | `GetJob` + `CancelJob` | `job_id` | 取消由服务确认；已执行的 GPU 作业可能无法取消，不能把断开观察当成停止 |
| `download` | `GetJob` + `DownloadArtifact` | 已成功的 `job_id` | 仅下载该作业清单中的产物到当前工作区；返回实际文件路径和展示引用 |

`RunTasks` 是原生协议的双向流式方法，当前 DSH 工具没有单独的 `action`。这属于尚未映射的协议能力，不是失效接口，也不需要删除协议定义。

任务与资产使用不同身份：`operation_id` 是 BenchForge 一次工具调用记录；`job_id` 是原生作业；原生 `session_id` 是仿真场景会话，与 DSH 会话 ID 不同。作业、上传资产和 retained scene/world 的归属按工作区、DSH 会话及端点保存于 PostgreSQL。其他会话不能凭外部 ID 下载、控制或覆盖资源。严格会话模式拒绝没有独立世界身份的全局操作；后文原生任务表描述协议能力，不代表全部原生任务都对受限模型开放。

上传先冻结会话文件，再校验其依赖闭包。USD、材质、纹理、URDF/MJCF 和 glTF 等不能携带越界路径或未授权外链；必要解析器缺失时拒绝上传。同一 owner 的新会话可通过授权资产版本复用生成网格、材质和纹理，不读取原对话目录。资产共享也不自动授予原仿真世界的控制权。

USD 上传检查复用现有图层解析，在回执的 `warnings` 中列出最多 16 处带标量 RGB 默认值的 `primvars:displayColor`，包含图层文件名和属性路径。A100 材质读取器把该属性当作颜色数组。整物体底色可将原 RGB 写成 `color3f[] primvars:displayColor = [(r, g, b)] (interpolation = "constant")`，然后上传、导入新版本并实际采集。检查只读取上传图层和 USDZ 包中的图层成员，保留上传字节与原回执。[上传用法和示例](../src/dsh/skills/isaac-sim/references/tool-api.md#上传提交观察下载)

### SceneProgram 引用本会话原生资产

`benchforge_isaac_sim(action=upload)` 返回的 `asset_id` 和 `relative_path` 可以直接写入对象的 `native_asset`，对象使用 `kind="native"`。该字段还要求源坐标下以米计的 `size:[x,y,z]` 和 `source_up_axis:"Y"|"Z"`，可选 `fix_base`。对象自身的 `size` 则是场景 Z-up 下的目标尺寸。适配器转换源尺寸轴向后计算缩放，沿用 `asset.import` 和 `scene.edit`，不要求修改源码目录、静态资产表或部署配置。

句柄在预检、任务准入及后台编译时核对会话和服务端点归属。通用身份恢复在 `src/service_access/task_access.py`，数据合同在 `src/capabilities/scene_authoring/catalog/native_reference.py`，权限校验在 `src/capabilities/isaac_sim/scene_requirements.py`，导入编排在同目录的 `authoring.py` 和 `capture_stage.py`。有可信会话身份的任务提交不能因漏传 context 而退为无身份维护任务。

`native_assets` 目录的 `configured`、`unconfigured`、`unknown` 只说明提供者绑定状态；目录不承诺文件存在、渲染成功、关节有效或飞行控制可用。上传及静态预检也不构成视觉或机器人验收。`benchforge_mesh_import` 仅导入 GLB/glTF/OBJ/PLY/STL 静态网格；USD、URDF/MJCF 等在提交前返回明确的原生导入指引，避免排队转换失败和关节信息丢失。

### 同一网关内的两个操作族

当前部署使用同一 gRPC 网关组合两套服务：A100 retained scene 的 8 项标准操作用于 SceneProgram 采集；原有 20 项扩展操作保留在 `extended.` 命名空间中，用于多世界及机器人功能。正式 `50051` 已于 2026-11-19 切换到 A100 网关，原预览 `50053` 已退出；这只描述该实例，其他部署仍须核对在线接口并逐项验收。

| 操作族 | `GetCapabilities` 名称 | `submit.request.extension.operation` | `GetApiCatalog` / `invoke.feature_id` |
| --- | --- | --- | --- |
| A100 标准场景 | `extension.asset.import`、`extension.scene.capture` 等 | `asset.import`、`scene.capture` 等 | `sessions.asset.import`、`sessions.scene.capture` 等 |
| 保留的扩展操作 | `extension.extended.asset.import`、`extension.extended.world.collect` 等 | `extended.asset.import`、`extended.world.collect` 等 | `sessions.extended.asset.import`、`sessions.extended.world.collect` 等 |

表中的名称必须在当前服务目录中实际存在才能使用。两套同名后缀的 schema 和实现边界可能不同：标准 `asset.import` 接受的参数不能照搬给 `extended.asset.import`。`session_id` 必须始终送回创建它的操作族；相同网关不代表共享场景会话。当前 BenchForge 生产采集走标准操作，不能在一次失败后把该场景会话直接改交给扩展操作尝试。

该实例部署拓扑使用 `127.0.0.1:50051` 统一入口，标准场景服务为内部 `18324`，原多世界服务 `8323/8324` 保留。这些地址属于该实例部署配置；DSH 与 BenchForge 客户端使用配置的 gRPC 端点，不探查内部 HTTP。网关组合及命名空间属于部署适配，不能据此宣称仅启动原始 A100 脚本就会自动拥有两套操作。

工具下载按原生操作标记产物阶段：两族的 `scene.capture` / `scene.export` 对应 S2，`world.collect` / `extended.world.collect` 对应 S3。没有明确映射的操作保留无阶段，不通过文件名猜测为已完成的生产结果。

### 最小调用顺序

以下为 DSH 工具参数示例。`<job_id>` 等需使用真实返回值，不是字面字符串；工作区由 Host 注入。

先读取工具自带的使用说明；这不需要模型进入服务源码目录找文档：

```json
{"action":"guide"}
```

读取某项原生操作的完整在线 schema：

```json
{"action":"guide","operation":"extension.asset.import"}
```

`guide` 默认仅返回操作摘要与产品域计数，避免把整套目录塞入上下文。使用完整 `operation` 可读取单个能力的完整 `details.parameter_schema`；使用 `feature_id` 可读取对应产品功能的完整 `request_schema`。例如，读取扩展操作的说明时选择 `extension.extended.world.collect`，通过产品入口调用则使用目录实际返回的 `sessions.extended.world.collect`。查不到的名称会明确报错，不生成猜测文档。

需要精确检查时再读取原始能力与目录：

```json
{"action":"capabilities"}
```

```json
{"action":"catalog"}
```

先确认所需操作可用，再在工作区保存 USD 文件并上传：

```json
{"action":"upload","source_path":"tasks/example/work/scene.usda"}
```

按上传结果提交独立场景导入。`extension` 操作名不带能力列表中的 `extension.` 前缀：

```json
{
  "action":"submit",
  "request_id":"example-scene-import-1",
  "request":{
    "extension":{
      "operation":"asset.import",
      "arguments":{
        "source":{"asset_id":"<上传返回的asset_id>","relative_path":"<上传返回的relative_path>"},
        "path":"/World/Scene"
      }
    }
  }
}
```

```json
{"action":"wait","job_id":"<job_id>","timeout_seconds":30}
```

只有 `job.state` 为 `SUCCEEDED` 才使用结果中的 `session_id` 进行后续步进、采集和导出。下载针对产生所需文件的成功作业：

```json
{"action":"download","job_id":"<成功采集或导出作业的job_id>"}
```

丢失提交响应时，复用相同 `request_id` 和完全相同的参数。修改场景或参数时使用新的请求身份，不覆盖原请求。`SUCCEEDED` 只证明这项原生操作成功；BenchForge 的场景验收和生产阶段审核仍需独立通过。

## 原生任务分类

`submit.request` 只能选择协议 `oneof task` 中的一项，例如 `{"snapshot":{}}`。下表列全该协议的任务字段；它描述服务的路由和实现范围，**不表示这些服务在当前实例全部已启动或已经验收**。`invoke` 则通过在线产品目录的 `feature_id` 选择同一类任务，目录可以进一步限定参数。

| `request` 中的任务字段 | 服务端实现范围 |
| --- | --- |
| `simulate`、`randomize` | 独立刚体场景及 CUDA 渲染；随机化是带种子的独立采样，不保证自动排除碰撞 |
| `render_asset` | 预加载 mesh/Gaussian 资产渲染，不是 retained scene 的相机接口 |
| `snapshot`、`step`、`control`、`dynamics` | 预配置机器人世界的状态、步进、关节控制及动力学；基础 control 路径面向配置中的两关节机械臂，不等于任意机器人控制 |
| `scan`、`capture` | 机器人世界的平面碰撞几何扫描和图像/标注；scan 为 180 束，不是光学雷达信号仿真 |
| `gripper`、`trajectory`、`inspect_gains` | 约束式抓取、已配置机械臂的轨迹执行、关节与有效惯量检查；不承诺原生 Surface Gripper 或自动增益优化 |
| `differential_drive`、`rl_step` | 轮速命令计算、批量观测/奖励步；前者不包含移动机器人动力学，后者不包含训练算法或 Isaac Lab 集成 |
| `cloth` | 独立 Newton XPBD 布料，不与 PhysX 双向耦合 |
| `export_scene`、`ros2_status` | 导出机器人世界；检查已配置 ROS 2 桥及计数，不代表 Nav2/SLAM 已可用 |
| `import_robot`、`newton`、`convert_asset` | 独立进程中的 URDF/MJCF 转 USD、XPBD/MuJoCo 数值验证、GLB 材质烘焙转换；与 retained scene 的 `asset.import` 不同 |
| `extension` | 服务注册的命名操作及其参数；客户端不能借此加载任意 Python 代码 |

## 场景采集使用的原生操作

能力列表中的名称是 `extension.<operation>`；提交时写 `request.extension.operation=<operation>`。下表描述 A100 的 retained scene 实现，不把其他机器人或扩展会话的参数混入同名场景操作。上线时须确认同名操作只有一个明确路由，场景 `session_id` 必须回到创建它的实现。

| 操作 | BenchForge 使用方式 | retained scene 实现的参数与边界 |
| --- | --- | --- |
| `asset.import` | 导入编译后的 USD，再逐项导入明确配置的原生资产 | `source` 为上传返回的 `asset_id` 和 `relative_path`；支持 `path`、可选 `session_id`、`fix_base`、`mass_kg`、`physics`、`source_up_axis`。接收 USD 系列文件，不接受 URDF、MJCF、GLB 或高度图；具体导入语义见下文 |
| `scene.edit` | 布置原生资产位姿、尺度和语义名称 | `session_id`、`changes`；至多 256 项，仅支持 transform 和 `a100sim:semanticLabel` 字符串设置，不是任意 USD 编辑器 |
| `physics.step` | 场景沉降，保存 CUDA 测得的位姿和速度 | `session_id`、`steps`、`dt`；步数 1–1200，`dt` 为 0.0001–0.02 秒 |
| `world.observe` | 获取刚体观测，生成物理回执 | `session_id`、可选 `bodies`、`imu=false`、`contacts=false`；必须在最后一次场景修改后完成步进。此实现不提供 `robots` 参数或 IMU/接触观测 |
| `scene.inspect` | 分页读取 prim、变换和碰撞属性 | `session_id`、`limit`、`cursor`；每页至多 2000 项，无 `path` 过滤。`has_more=true` 时原样传回 `next_cursor` 字符串 |
| `scene.capture` | 采集已完成物理步进的场景及其标注 | `session_id`、`cameras`、`size`、`spp`、`annotations`；每批 1–32 个已定义相机，64–2048 像素，1–1024 spp。BenchForge 对更多机位分批，不丢弃用户的机位 |
| `scene.export` | 获取真实几何、USD 场景及依赖包 | `session_id`、`format=usd|usdz`，没有 `zip` 格式；下载返回作业清单中的文件，不直接读取服务端路径 |
| `scene.close` | 结束已不用的场景会话 | `session_id`、可选 `delete=false`；保留场景与历史产物，不承诺删除 |

`asset.import` 不传 `fix_base` 时保留原始 USD 引用；显式传入时才归一资产单位、调整几何中心并配置碰撞，`fix_base=false` 还创建动态刚体。`mass_kg` 仅对该动态刚体生效。`physics.static_friction`、`dynamic_friction` 和 `restitution` 是接触参数；`physics.collider` 与 `mass_source` 只保留调用方元数据，不改变碰撞器选择或质量。不能因为字段可接收就推导它改变了物理行为。

服务公开的 schema 若只有 `{"type":"object"}`，不能解释为任意参数均受支持，也不能仅因它没有列出某个字段就断言该字段不受支持。完整字段说明应由服务端维护并返回；执行失败需依据具体错误及原始请求。机器人控制、关节轨迹、移动机器人和多设备观测使用各自在线目录描述，不从刚体场景采集推导这些能力。

BenchForge 可并发提交独立场景并限制在途数量；这不等于每个仿真进程都并行执行。该 retained scene 实现通过单进程主线程队列串行执行 Kit 操作，单次采集依次渲染相机。并发吞吐、延迟及多个场景隔离必须由部署实测，不能仅凭 `max_concurrent_captures` 或工具返回速度做承诺。

## 源码定位

下列 BenchForge 链接相对本仓库根目录。仿真器链接指向独立仓库 `IsaacSim4A100` 的 `A100` 分支；使用子模块检出时，均位于 `third_party/IsaacSim4A100/` 下。

| 责任 | BenchForge 源码 |
| --- | --- |
| 工具动作、参数和给模型的说明 | [src/dsh/tools/benchmark/contracts.mjs](../src/dsh/tools/benchmark/contracts.mjs) |
| DSH 原生工具注册 | [src/dsh/tools/benchmark/tools.mjs](../src/dsh/tools/benchmark/tools.mjs) |
| 工作区核验、Python 执行、取消与进程清理 | [src/dsh/host/python_tools.mjs](../src/dsh/host/python_tools.mjs) |
| Python 工具分发和操作回执 | [src/server/operations.py](../src/server/operations.py)、[src/storage/workspace_jobs.py](../src/storage/workspace_jobs.py) |
| `benchforge_isaac_sim` 动作实现 | [src/capabilities/isaac_sim/tool_api.py](../src/capabilities/isaac_sim/tool_api.py) |
| 模型可调用的说明与在线目录组合 | [src/capabilities/isaac_sim/guide.py](../src/capabilities/isaac_sim/guide.py) |
| 可按需加载的正式 Skill 及包内引用 | [src/dsh/skills/isaac-sim/SKILL.md](../src/dsh/skills/isaac-sim/SKILL.md) |
| gRPC 连接、观察及资产传输 | [src/capabilities/isaac_sim/grpc/transport.py](../src/capabilities/isaac_sim/grpc/transport.py) |
| 请求身份、冻结参数与断点恢复 | [src/capabilities/isaac_sim/grpc/tasks.py](../src/capabilities/isaac_sim/grpc/tasks.py)、[receipts.py](../src/capabilities/isaac_sim/grpc/receipts.py) |
| 原生作业归属 | [src/storage/native_jobs.py](../src/storage/native_jobs.py) |
| retained world/上传资产归属和上传依赖检查 | [resource_access.py](../src/capabilities/isaac_sim/resource_access.py)、[upload_access.py](../src/capabilities/isaac_sim/upload_access.py) |
| `capture/refine/extend/retry` 注册 | [src/dsh/tools/scene/tools.mjs](../src/dsh/tools/scene/tools.mjs) |
| SceneProgram 编译与采集执行 | [authoring.py](../src/capabilities/isaac_sim/authoring.py)、[capture_stage.py](../src/capabilities/isaac_sim/capture_stage.py)、[evidence.py](../src/capabilities/isaac_sim/evidence.py) |
| 后台观察、通知与续行 | [src/dsh/tools/scene/capture/capture_runtime.mjs](../src/dsh/tools/scene/capture/capture_runtime.mjs) |
| 客户端使用的协议副本 | [src/capabilities/isaac_sim/grpc/simulator.proto](../src/capabilities/isaac_sim/grpc/simulator.proto)；同目录 `protocol.json` 仅记录来源 |
| 预设及角色说明 | [src/dsh/presets/preset_definition.py](../src/dsh/presets/preset_definition.py) |

| 责任 | IsaacSim4A100 源码（`A100` 分支） |
| --- | --- |
| 对外 gRPC 协议 | [a100/a100sim/rpc/v1/simulator.proto](https://github.com/EurecaMoment/IsaacSim4A100/blob/A100/a100/a100sim/rpc/v1/simulator.proto) |
| gRPC 方法及后端选择 | [a100/a100sim/rpc/server.py](https://github.com/EurecaMoment/IsaacSim4A100/blob/A100/a100/a100sim/rpc/server.py) |
| 产品功能目录及 `InvokeFeature` 映射 | [a100/a100sim/rpc/catalog.py](https://github.com/EurecaMoment/IsaacSim4A100/blob/A100/a100/a100sim/rpc/catalog.py) |
| 基础任务路由、能力边界与独立工作流 | [a100/a100sim/rpc/backend.py](https://github.com/EurecaMoment/IsaacSim4A100/blob/A100/a100/a100sim/rpc/backend.py)、[workflows.py](https://github.com/EurecaMoment/IsaacSim4A100/blob/A100/a100/a100sim/rpc/workflows.py) |
| 作业队列、状态和回执 | [a100/a100sim/rpc/jobs.py](https://github.com/EurecaMoment/IsaacSim4A100/blob/A100/a100/a100sim/rpc/jobs.py) |
| 资产边界与产物清单 | [a100/a100sim/rpc/storage.py](https://github.com/EurecaMoment/IsaacSim4A100/blob/A100/a100/a100sim/rpc/storage.py) |
| retained scene 操作注册、资产解析与服务路由 | [a100/a100sim/rpc/scenes.py](https://github.com/EurecaMoment/IsaacSim4A100/blob/A100/a100/a100sim/rpc/scenes.py) |
| 场景服务生命周期和队列 | [a100/a100sim/scenes/service.py](https://github.com/EurecaMoment/IsaacSim4A100/blob/A100/a100/a100sim/scenes/service.py) |
| 导入、编辑、物理、观测、采集、导出实现 | [a100/a100sim/scenes/sessions.py](https://github.com/EurecaMoment/IsaacSim4A100/blob/A100/a100/a100sim/scenes/sessions.py) |
| 场景渲染 | [a100/a100sim/usd_renderer.py](https://github.com/EurecaMoment/IsaacSim4A100/blob/A100/a100/a100sim/usd_renderer.py)、`a100/a100sim/scenes/materials.py`、`meshes.py` |
| 新场景服务与 gRPC 启动脚本 | [a100/start-scenes.sh](https://github.com/EurecaMoment/IsaacSim4A100/blob/A100/a100/start-scenes.sh)、[a100/serve-grpc.sh](https://github.com/EurecaMoment/IsaacSim4A100/blob/A100/a100/serve-grpc.sh) |

### 候选部署适配的来源

以下路径记录候选准备位置，供维护者复现；不表示已经切换正式服务，也不属于通用客户端配置或模型判断运行时能力的依据：

- 组合发布目录：`/home/jiangbaoyang/github/BenchForgeV2/.deployment/g3005/isaac-a100/releases/a100-34876f47-20261118/`。
- 该发布目录中的 `a100sim/rpc/extensions.py` 在启用 `A100SIM_SCENE_URL` 时为原 20 项操作添加 `extended.` 命名空间，并将标准合同合入供 `catalog.py` 使用的操作集合；原基础 `backend.py` 不变。
- 可复现的命名空间补丁：`/home/jiangbaoyang/github/BenchForgeV2/.deployment/g3005/maintenance/isaac-20261118/extended-namespace.patch`。这是部署适配，不假定上游原始启动脚本自带此组合。
- `a100sim/rpc/scenes.py` 与新增 `a100sim/scenes/contracts.py` 的准确标准场景 schema 为本轮上游候选改动。当前候选源码位于维护工作树 `work/isaac-scene-contract-fix/a100/`；未合入上游前，不能把远端 `A100` 分支链接视为已包含该补丁。

此前候选改动通过的 12 项 CPU 与回环 gRPC 检查只覆盖合同和调用边界。2026-11-19 正式入口另完成 39 次真实工具操作、10 个作业，涵盖 URDF 依赖上传、机器人导入、GLB 转换、刚体步进、双相机图像与标注、下载、归属隔离和场景关闭；带质量/接触参数/坐标轴的原生导入成功，导出 USD 验证了指定质量。这不覆盖原生多机器人控制或全部扩展能力。标准 8 项操作不提供完整原生多机器人控制。

## 参数校验与服务切换核验

接口允许额外字段与字段是否实际生效是两件事。BenchForge 的原生 `request` 保持开放，仿真器决定具体操作合同；客户端不伪改在线 schema、不删除物理要求来迁就旧服务，也不静默忽略拼错字段。必要的文件边界、资源归属、单一任务类型和基础数据类型检查继续保留。

已修正四处适配误拦：上传目标 `relative_path` 保留为资产内部相对路径；布局请求接受 Host 注入的上下文；资产导入/转换不要求先建立仿真世界；计划文件接受布尔 `validate_only`，显式工具参数优先，业务字段仍禁止双来源冲突。

`src/capabilities/isaac_sim/capture_contract.py` 对配置端点的实时 `GetCapabilities` 做字段兼容性检查。所有原生对象共用同一参数构造；在上传和创建世界前一次报告缺失操作及明确被 `additionalProperties:false` 拒绝的字段。检查只覆盖适配器实际所需操作和对象字段，不是完整 JSON Schema 验证，递归检查对象和数组元素的显式字段限制，不推断开放元数据、组合 schema 或值域。`service_contract_mismatch` 明确归部署配置/适配器处理，并提示不要删除场景要求或原样重复采集。静态场景预检通过后补充一次只读服务检查，采集执行时按真实输出选择再核验；这些检查不代替渲染和物理验收。

部署切换应核对配置端点、该端点的在线合同和真实作业结果，不能仅验证另一个预览端口。切换前清空或等待相关队列，保留正式资产状态与端点身份；切换后逐项对账历史资产并撤下预览启动入口。2026-11-19 切换保留了原正式入口的 227 个资产。

## 维护与陈旧接口判定

| 内容 | 处理规则 |
| --- | --- |
| BenchForge 旧 HTTP 采集守护进程、旧连接配置、旧提供方拼写 | 不作为当前调用入口；保留一次性配置/历史迁移所需代码，避免删除历史数据 |
| 原生协议中的稳定 RPC、仍可查询的历史作业 | 不因某次没用到或尚未映射进工具而删除 |
| `benchmarkforge_*` 历史工具名 | 新文档只使用 `benchforge_*`；兼容旧对话的名称归一化不构成第二套工具注册 |
| 旧部署中与 retained scene 重名的扩展操作 | 标准 8 项保留无前缀操作名；原有扩展统一进入 `extended.`，两族独立会话、独立 schema。未完成的旧请求按原回执恢复，不能擅自换操作族重放 |
| 目录中的 `UNAVAILABLE` / `UNSUPPORTED` / `UNVERIFIED` | 如实展示原因，不当作已可调用的承诺；不为了让目录“全绿”删除真实能力边界 |
| `protocol.json` 的源码编号和摘要 | 保留协议来源追溯；运行代码不以它作为服务可用性门禁 |

部署验证至少覆盖：DSH 当前预设中能发现工具与说明、能力/目录返回一致、真实上传与导入、物理步进、观测、多相机标注、产物下载、请求重放与失败状态。只执行 CPU 协议测试不足以验收 CUDA 物理、图片质量或生产吞吐。真实验收报告应保存服务端点、日期、输入、作业 ID、产物和未通过项目；生产场景还需通过自身质量审核。

本说明不把尚未完成的部署验收写成成功。具体实例状态以维护者的实测报告和对应作业回执为准。
