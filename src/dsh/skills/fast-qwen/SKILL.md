---
name: fast-qwen
description: 使用已配置的 FastQwen 对话或标注模型，了解 BenchForge 模型查询、推理输入、输出和预测边界；不用于启动或修改模型服务。
---

# FastQwen

FastQwen 可以作为 DSH 对话模型，也可以提供 BenchForge 的标注模型角色。
当前对话由 DSH 的模型路由调用；不要为了继续对话再调用一次标注模型。
标注角色可能覆盖成其他模型，不能仅凭本 Skill 的名称声称某次推理由 Qwen 完成。

## 工具与能力

模型工具存在时，使用 `benchforge_model_infer`。它调用部署已选择的
`roles.annotation` 路由，不接收任意服务器地址、凭证或模型切换参数。
工具未注册或角色未配置时报告缺口，不读取 `third_party`、配置文件或凭证。

| 参数 | 含义 |
| --- | --- |
| `workspace` | 当前 DSH 会话绑定的工作区，由宿主校验。 |
| `action` | `models` 查询配置端点的模型列表；`infer` 请求推理。 |
| `payload` | `infer` 的 OpenAI Chat Completions 请求内容，包括 `messages`；实际 model 由部署覆盖。 |
| `timeout_seconds` | HTTP 请求等待时间，默认 300 秒；不是输出 token 数量或上下文大小。 |

```json
{"workspace":"<当前会话工作区>","action":"models"}
```

```json
{"workspace":"<当前会话工作区>","action":"infer","payload":{"messages":[{"role":"user","content":"根据以下已提供的观测记录提出语义标注建议，并列出不确定项：<实际记录>"}]}}
```

多模态输入使用模型实际支持的消息格式。普通服务器文件路径不自动转换成图像；
只能提供获准图像的有效内容引用。`models` 列表不保证视觉、最长上下文或某个推理档位可用。
使用已配置的默认推理强度，不猜测或降低档位，不用缩减用户要求绕过输入错误。
此适配器读取完整 JSON 响应，不使用 `stream:true`。

## 结果与失败

调用同步返回 `service_result`，并包含 `evidence_kind: "model_prediction"`、
`gt_written: false`，外围工具还返回工作区内的操作回执。保存实际响应与输入来源；
响应中的 model、usage 和 finish_reason 只有存在时才报告，不补造计数。

预测只能辅助标注和修复，不能改写仿真真值或代替场景验收。先汇总同一批已知问题再请求，
避免逐对象重复试错；输出被截断时保留已完成结果，再按独立任务续做。
参数或图像格式错误先核对响应和实际工具合同；连接错误交维护者，禁止自行检查源码、
启动服务或修改路由。超时不证明服务没有执行，重试前记录重复推理的可能性。
