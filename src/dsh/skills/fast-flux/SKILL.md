---
name: fast-flux
description: 使用已配置的 FLUX 图像生成与编辑工具制作场景设计参考或对象参考图，核验异步产物并交接分割、重建与仿真。
---

# FLUX 图像生成与编辑

在需要生成场景设计参考、单个对象参考图，或编辑已有图像时使用。图像只能表达设计意图，不提供真实几何、物理状态或真值标注（GT）。

## 能力发现与访问边界

先调用 `benchforge_scene_catalog({"section":"generation_tools","item_id":"diffusion"})`，并以当前工具 schema 为准。提供方由部署配置决定；目录存在不代表服务当前健康。仅调用已注册工具，不读取 `third_party`、部署源码、配置或凭证，不猜地址、端口或模型名，不自行启动服务。只读写本会话工作区及通过工具明确提供的任务资源；输入路径原样复制工具回执的 `source_path`，路径不被接受时修正交接或报告维护需求，不绕过路径规则。

## 主接口：benchforge_diffusion

| 参数 | 用法 |
| --- | --- |
| `prompt` | 必填、非空文本；描述主体、构图、材质、光照与需要保留或修改的内容。 |
| `source_image` | 可选。已有且获准使用的图像 `source_path`；提供时执行图像编辑，省略时文生图。 |
| `width`、`height` | 可选正整数。省略时使用已配置默认值；服务仍可校验自身支持的尺寸。 |
| `steps` | 可选正整数。省略时沿用已配置推理步数，不为排障擅自降低质量。 |
| `seed` | 可选整数，用于记录和重现请求；相同 seed 不构成跨版本像素完全一致的承诺。 |
| `request_key` | 可选请求身份。同一提交重取回执时保持请求和身份一致；修改 prompt 或其他输入后发起新请求。通常省略，由宿主生成。 |

当前工具每次生成一张图，不暴露多图输入、`n`、`guidance_scale` 或其他提供方专用字段。不要把服务底层参数直接塞进 DSH 工具。

```json
{"prompt":"真实室内参考图：完整客厅布局、明确的通行空间，木质家具与布艺沙发，柔和自然光，无文字标记"}
```

编辑示例中的占位符必须替换为实际回执，不能直接提交：

```json
{"prompt":"保留房间视角与家具位置，将沙发改为深蓝布料，保留自然光和接触阴影","source_image":"<已返回的图像 source_path>"}
```

## 获取实际结果

1. 提交返回 `run_id`、`task_id`、`state:"accepted"`；这只是受理。保存两种 ID，状态查询使用 `run_id`，证据读取使用 `task_id`。
2. 独立生成任务当前不自动注册采集观察器。调用 `benchforge_wait({"run_id":"<run_id>","background":true})` 注册观察，随后推进独立工作；也可有界等待 `timeout_seconds:30`，不要高频轮询或重复提交。
3. 通过 `benchforge_scene_status({"run_id":"<run_id>"})` 检查任务状态和实际失败。只有任务 `SUCCEEDED` 后才按成功消费结果；需要完整字段时加 `detail:true`。
4. 调用 `benchforge_scene_evidence({"task_id":"<task_id>","file":"generation_stage.json"})`。结果包含 `tool:"diffusion"`、`status`、`gt_source:false`、实际 `parameters`、`worker`、图像 `source_path` 和 `files`。
5. 从 `files` 复制证据文件名，用 `benchforge_scene_evidence` 打开图像核对构图、对象完整性、材质和编辑保留项。不要根据生成成功或文件存在宣称内容验收通过。

## 后续交接

- 需要对象资产时，将图像 `source_path` 交给 `benchforge_scene_sam3`，选定遮罩后交给 `benchforge_trellis`；不必为了调用小模型而无目的地串行跑完整链条。
- 需要场景参考时，可把这张图以 `layout:{"mode":"reference","source_image":"<source_path>"}` 交给支持 layout 的场景工具。它伴随真实仿真结果接受对照检查，不替代 SceneProgram 或 Isaac Sim 捕获。
- 新建图像驱动场景时优先调用 `benchforge_layout`，并提供 `layout.objects` 清单。该流程使用配置中的 FastFLUX 准备设计图，再调用 FastSAM3、FastDA3 和 FastTrellis，返回分割叠图、对象资产及可编辑摆放估计。随后模型检查证据并调整完整 SceneProgram。已有合适图像时通过 reference 模式复用，不反复生成。

## 失败处理

输入路径错误先核对回执、文件来源与本会话权限；不要猜家目录或搜索部署文件。参数错误只修正报错字段。排队或资源等待保留原 run；连接中断先查状态，避免生成重复任务。图像不合要求时先汇总可见问题，再一次性修改 prompt/参考图并提交新请求，保留旧证据。提供方未配置、不可达或持续失败时报告具体工具错误及 run ID，交维护者处理，不读取配置或绕过工具直接请求服务。停止时使用 `benchforge_cancel({"run_id":"<run_id>"})`，再核对状态。

所有输出都是生成内容，不是相机观测、场景验收或 GT。
