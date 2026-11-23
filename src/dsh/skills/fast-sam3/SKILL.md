---
name: fast-sam3
description: 使用 SAM3 对已提供图像做文本或像素框提示分割，检查遮罩、框与分数，为对象重建提供明确选中的输入。
---

# SAM3 图像分割

在需要定位图像中的指定主体、生成对象遮罩或为三维重建选择实例时使用。遮罩、标签、框和分数都是模型预测，不能替代仿真器的实例/语义 GT。

## 能力发现与访问边界

调用 `benchforge_scene_catalog({"section":"generation_tools","item_id":"sam3"})` 并核对当前 `benchforge_scene_sam3` schema。服务与模型由部署配置选择；不要读取 `third_party`、部署源码、配置或凭证，也不直连猜测的地址。只使用本会话工作区和工具明确提供的任务资源。`source_image` 必须来自获准输入或实际工具回执，按原文传递，不通过扩大文件访问修复路径错误。

## 接口：benchforge_scene_sam3

| 参数 | 用法 |
| --- | --- |
| `source_image` | 必填，待分割图像的真实 `source_path`。 |
| `prompts` | 可选字符串数组，如 `["chair","table"]`；以语义描述寻找对象。 |
| `instances` | 可选对象数组。框提示实例应提供 `id`（整数）、`label`（文本）、`box`（像素 `[x0,y0,x1,y1]`）；可用 `segmentation_prompt` 补充描述。 |
| `confidence` | 可选数值，作为检测阈值；省略沿用服务配置。根据实际漏检/误检证据调整，不把分数当正确概率或验收结果。 |
| `request_key` | 可选稳定请求身份；相同身份仅对应相同请求。修改图像、提示或框后使用新请求。 |

`prompts` 和 `instances` 至少有一个非空。框坐标必须对应这张原始输入图的像素坐标；不要把缩略图坐标、归一化坐标或另一张图的框直接传入。虽然实例 schema 未把每个子字段标成必填，当前服务适配器会使用 `id`、`label`、`box`，三者都应提供。

文本示例：

```json
{"source_image":"<实际图像 source_path>","prompts":["chair","table"]}
```

框提示结构示例，数字仅示意，须替换为在当前图像上检查过的框：

```json
{"source_image":"<实际图像 source_path>","instances":[{"id":1,"label":"chair","box":[120,80,620,700],"segmentation_prompt":"the complete chair"}]}
```

当前接口仅处理一张图，不提供视频跟踪或跨图实例身份保证。文本模式保留服务返回的所有非空检测；框模式当前按分数与框匹配排序，每个实例选一个候选，结果可附带 `candidate_count`、`selection_margin`。不要声称框模式返回全部候选，也不要据低选择间隔强行确认对象身份。

## 异步结果与检查

1. 保存提交返回的 `run_id`、`task_id`；`accepted` 不表示分割完成。
2. 独立生成任务需显式调用 `benchforge_wait({"run_id":"<run_id>","background":true})` 注册观察，或用 `timeout_seconds:30` 有界等待。继续独立工作，不高频轮询。
3. 用 `benchforge_scene_status` 查看状态；任务成功后调用 `benchforge_scene_evidence({"task_id":"<task_id>","file":"generation_stage.json"})`。
4. 读取 `segments`：每项含 `id`、`label`、`text_prompt`、`score`、`box`、遮罩文件名 `mask` 与遮罩 `source_path`。框模式还有其候选选择信息。`files` 是实际产物清单，`gt_source:false` 明示预测属性。
5. 用证据工具查看选定遮罩及原图，检查对象是否完整、是否混入相邻对象、边界是否切掉肢体/细部。需数值处理时仅使用获准的工作区副本，不能修改原始生产证据。

## 交给重建

选定遮罩后调用 `benchforge_trellis`，使用同一张 `source_image`，并在 `objects` 中给出唯一 `object_id` 和选定遮罩的 `source_mask`。不要传 `mask_0000.png` 这种自行拼接的裸文件名，应传回执里的完整 `source_path`。遮罩必须和原图尺寸一致；多个主体分别选择，不合并成一个对象后声称可独立控制。

新建场景的初始布局使用 `benchforge_layout`，后端会在同一参考图上运行 SAM3 与 FastDA3，并自动把普通可见对象交给 TRELLIS 和资产登记。独立 SAM3 工具仍适用于已有图像上的定点分割、遮罩重做和人工选定实例。

## 空结果、错误与修复

`segments:[]` 即使任务 `SUCCEEDED` 也表示没有可用遮罩，不能继续伪造遮罩或成功交接。先核对主体是否可见、图像是否正确以及提示是否过宽；对象重叠或歧义时改用对应像素框。把漏选、混选、切边等问题一并整理后重试。输入调整发新请求，保留之前证据；提供方资源等待则保留原 run，不重复发任务。

路径拒绝时核对回执与授权资源；服务不可达或未配置时记录实际错误和 ID，报告维护需求，不读取配置、凭证或源码排障。停止使用 `benchforge_cancel({"run_id":"<run_id>"})` 并核对终态。输出预测可辅助制作资产和视觉审查，不能直接作为基准答案或官方标注。
