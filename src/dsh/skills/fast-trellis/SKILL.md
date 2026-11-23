---
name: fast-trellis
description: 使用 FastTrellis2 将选定图像对象重建为已登记的 Y-up PBR 网格，核验遮罩与网格产物，并说明尺度、物理和仿真验收边界。
---

# FastTrellis2 对象重建

在已有对象图像及明确遮罩、需要生成可放入场景的三维网格时使用。输出是生成资产，不是实测几何、真实物理本体、可控制机器人或已验收场景。

## 能力发现与访问边界

先读 `benchforge_scene_catalog({"section":"generation_tools","item_id":"trellis"})`。该工具会登记生成的网格并返回 `asset_id`；仅当要导入另一份独立用户网格时才查 `item_id:"mesh_import"`。以当前工具 schema 为准。模型与服务由部署配置选择，不读取 `third_party`、部署源码、配置或凭证，不猜地址、不直接轮询提供方。只使用本会话工作区和工具明确提供的任务资源，文件路径原样复制回执。

## 重建接口：benchforge_trellis

| 参数 | 用法 |
| --- | --- |
| `source_image` | 必填，一张原始图像的真实 `source_path`。 |
| `objects` | 必填、非空数组；每项是同一张图中的一个选定对象。 |
| `objects[].object_id` | 必填非空字符串，同一请求内唯一，不得为 `.`、`..`，不得含 `/` 或 `\`。 |
| `objects[].source_mask` | 必填，所选遮罩的真实 `source_path`，尺寸须与 `source_image` 相同。 |
| `objects[].seed` | 可选整数，优先于顶层 seed。 |
| `seed` | 可选整数，为未单独指定 seed 的对象提供默认值。 |
| `request_key` | 可选稳定请求身份。相同身份只用于同一请求；改变图像、遮罩或对象选择后新建请求。 |

```json
{"source_image":"<图像 source_path>","objects":[{"object_id":"chair_a","source_mask":"<已检查的椅子遮罩 source_path>"},{"object_id":"table_a","source_mask":"<已检查的桌子遮罩 source_path>"}]}
```

提交前检查原图和遮罩是否匹配、遮罩是否完整覆盖目标且排除邻居。当前适配器会把图像和遮罩配对补边到 1024×1024 再交提供方，保留对应关系；不是简单要求用户先把原图拉伸成方形。`objects` 支持多个对象，但当前适配器逐对象提交和收集，不保证 GPU 并行。不要添加未暴露的面数、材质、稀疏结构或多视角参数。

## 异步结果与恢复

1. 保存提交返回的 `run_id`、`task_id`；`accepted` 不是重建完成。
2. 独立生成任务需显式调用 `benchforge_wait({"run_id":"<run_id>","background":true})` 注册观察，或用 `timeout_seconds:30` 有界等待。提供方作业的跟踪由后端完成，模型不直接操作其 job URL。
3. 查看 `benchforge_scene_status`；任务成功后读取 `benchforge_scene_evidence({"task_id":"<task_id>","file":"generation_stage.json"})`。
4. `assets` 每项包含 `object_id`、网格文件名 `asset`、`source_path`、`source_frame:"y_up"`、实际 `model`、`provider_job_id`、`metric_reconstruction:false`、已登记的 `asset_id` 和 `registration`；`files` 列出实际 GLB 等证据，`gt_source:false` 明示生成属性。
5. 服务中断后先查询原 run。后端保存提供方 job 回执以便恢复，不要因为暂时没有 GLB 就重复提交同一对象。需要恢复已失败阶段时依据失败信息使用 `benchforge_retry`，不要用 retry 覆盖已经冻结的不同输入；修正输入应新建生成请求。

## 在 SceneProgram 中使用已登记资产

TRELLIS 任务成功后，等待状态为 `SUCCEEDED` 并读取 `generation_stage.json` 中对应对象的 `assets[].asset_id`。将该值直接填入 SceneProgram 的 `asset_id`，并将对象设为 `kind:"mesh"`。不要再次把同一个 TRELLIS 输出交给 `benchforge_mesh_import`，否则会重复转换和登记。

`benchforge_mesh_import` 用于导入单独提供的 GLB/glTF/OBJ/PLY/STL 静态网格。TRELLIS.2 GLB 使用 `y_up` 坐标系；导入外部文件时按实际来源声明坐标系，不要把 `y_up` 错报为 `sam3d_camera`。`source_gaussian` 仅用于已有配对数据，不意味着本次 TRELLIS 调用会生成 Gaussian。可以通过 `benchforge_scene_catalog({"section":"generated_assets","item_id":"<asset_id>"})` 查询登记资产详情。

## 尺度、朝向与验收

重建尺度未实测，Y-up 只声明向上轴，不证明物体实际摆正或语义朝向正确。`size_hint_m` 是期望的尺寸包络，不是测量证据；场景摆放需明确实际尺寸与姿态，保持源模型比例，检查生成网格的边界和渲染效果。注册成功只表示转换和登记完成，不提供视觉质量验收。

在 SceneProgram 中使用资产后，先批量预检相关摆放和合同问题，再通过 Isaac Sim 捕获验证外观、支撑、碰撞和交互。静态机器人外形不能当作带关节、驱动和传感器的真实机器人模型；缺少控制本体时使用仿真器公开目录查可用本体，不能用生成外壳冒充。

新建场景的图像布局可通过 `benchforge_layout` 自动为普通可见对象执行 TRELLIS 重建和登记，并在 `layout_estimate.json` 返回 `asset_id` 与可编辑几何估计。此时不要为同一对象重复调用重建或导入。独立 `benchforge_trellis` 仍用于新增对象、修复遮罩后重建和补充资产。

## 常见错误处理

提交超时且未收到模型任务 ID 时，受理状态未知。保留原任务回执交维护者核对，不要新建相同请求。已有模型任务 ID 时，BenchForge 会在配置的超时预算内重试状态查询；持续超时应报告原 ID，便于恢复查询。

图像与遮罩尺寸不一致时，回到对应 SAM3 输出，核对是否来自同一原图；不得凭猜测移动或拉伸遮罩。遮罩切边、混入背景导致网格缺失时，一次整理所有对象的可见问题并修正选择，再提交新请求。重复或非法 `object_id` 只修正标识字段。路径拒绝通过回执与授权资源交接解决，不搜索部署目录。资源等待保留原 run；持续服务错误报告实际错误与 ID，由维护者处理。停止用 `benchforge_cancel({"run_id":"<run_id>"})` 并核对终态。
