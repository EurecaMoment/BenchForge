---
name: fast-da3
description: 使用 FastDA3 估计单图米制深度并读取相机参数，正确处理可选位姿条件、数值数组和可视化，区分预测与仿真真值。
---

# FastDA3 深度估计

在需要对已有图像估计前后关系、深度结构或辅助检查几何时使用。输出标记为 `estimated_metric_depth`；即使单位为米，也属于模型预测，不能替代 Isaac Sim 原始深度或距离 GT。

## 能力发现与访问边界

调用 `benchforge_scene_catalog({"section":"generation_tools","item_id":"depth"})`，以当前 `benchforge_depth` schema 为准。仅使用已配置工具，不读取 `third_party`、部署源码、配置或凭证，不推断服务端口。仅访问本会话工作区及工具明确提供的任务资源；路径使用获准输入或实际回执的 `source_path`，不要拼写服务器目录。

## 接口：benchforge_depth

| 参数 | 用法 |
| --- | --- |
| `source_image` | 必填，一张图像的实际 `source_path`。 |
| `input_size` | 可选整数，140–1024，是提供方处理分辨率参数；省略使用已配置默认值，不等于限制原图文件尺寸。 |
| `intrinsics` | 可选 3×3 相机内参矩阵。 |
| `extrinsics` | 可选 3×4 或 4×4 世界到相机的外参矩阵。必须与 `intrinsics` 一同提供。 |
| `request_key` | 可选稳定请求身份；仅重取同一请求时复用，输入改变后新建请求。 |

当前 DSH 工具只接受单个 `source_image`。同时提供内外参会启用位姿条件，但不会把此工具变成多图批处理或多视角重建接口。矩阵来自可信相机记录；不知道相机时省略两者，不用单位矩阵或猜测数值冒充实测标定。

普通调用：

```json
{"source_image":"<实际图像 source_path>"}
```

位姿条件调用需在同一请求里传入真实的 `intrinsics` 和 `extrinsics` 数值数组。先确认来源坐标定义、矩阵尺寸和相机方向，不能直接把 camera-to-world 矩阵当作 world-to-camera。

## 异步结果与产物

1. 保存 `run_id`、`task_id`；返回 `accepted` 仅表示受理。
2. 独立生成任务需显式用 `benchforge_wait({"run_id":"<run_id>","background":true})` 注册观察，或用 `timeout_seconds:30` 有界等待；无需重复提交和高频查询。
3. 通过 `benchforge_scene_status` 判断完成和失败，任务 `SUCCEEDED` 后读取 `benchforge_scene_evidence({"task_id":"<task_id>","file":"generation_stage.json"})`。
4. 结果含深度数组 `source_path`、预览图 `visualization`、`depth_type`、`units`、`scale`、`files`、`gt_source:false`。`worker` 内还含 `depth`、`archive`、`intrinsics`、`extrinsics`、`extrinsics_convention`、`intrinsics_coordinates`、`pose_conditioned` 和实际 provider/model。
5. 从产物清单读取数值数组、NPZ 归档或预览图，原样使用返回路径。需要工作区计算时取得允许的任务副本，不从服务目录任意读取文件。

深度数值越大表示表面越远。预览图按当前数组的第 2–98 百分位归一化，**灰度不是米值，也不能跨图直接比较**。做测量必须读取数值数组，检查维度、有限值及相机坐标说明；不要假设数组尺寸与原图一致。返回的 `intrinsics_coordinates`、`extrinsics_convention`、`pose_conditioned` 应与数组一并保留。

## 使用边界与修复

- 可用于预测性几何分析、资产制作辅助和排查明显前后关系；回答必须标记模型估计及不确定性。
- 不能把预测深度当仿真深度写入 GT，也不能把相机深度、相机到表面的欧氏距离、世界高度混为一谈。真实采集所需含义应从仿真器接口与捕获元数据确认。
- 若只提供了一种矩阵，补齐真实的配套矩阵或同时去掉两者；分辨率错误按 140–1024 修正。结果异常先汇总图像内容、相机条件、数组范围与形状问题，再决定修正输入或请求，不盲目提高次数。
- 输入路径拒绝先校正真实回执及资源交接。资源等待保留当前 run；失败时读取失败记录，避免通过新请求重复消耗。服务不可达或未配置则报告具体错误和 ID，交维护者处理，不读取凭证或修改部署。
- 需要停止时调用 `benchforge_cancel({"run_id":"<run_id>"})`，核对任务终态后再结束。

将图像来源、请求参数、输出数组及相机信息一起保留，保证后续使用者能区分输入标定、模型返回值和真实仿真证据。
