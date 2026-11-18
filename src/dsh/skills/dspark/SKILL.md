---
name: dspark
description: 使用已配置的 DSpark4A100 模型角色进行对话与候选标注，了解实际工具接口、配置边界和结果来源；不用于部署模型。
---

# DSpark4A100

DSpark4A100 是可配置的语言模型服务提供方。DSH 对话模型由宿主路由选择；
模型服务的名称并不保证具体权重、上下文长度、多模态能力或推理档位。
不读取第三方源码、Git、部署目录或凭证来猜测这些值。

## 公共调用接口

需要额外候选标注时使用已注册的 `benchforge_model_infer`；当前对话自身由 DSH
调用，无需通过此工具递归调用自己。标注角色允许覆盖默认模型，不能把所有返回
统一称为 DeepSeek 结果。工具未配置则说明缺少标注入口，不自行找端口。

| 参数 | 含义 |
| --- | --- |
| `workspace` | 当前对话工作区。 |
| `action` | `models` 查询已配置端点；`infer` 提交标注推理。 |
| `payload` | OpenAI Chat Completions JSON，例如 `messages`；部署强制使用已配置的 model。 |
| `timeout_seconds` | 默认 300 秒，仅控制等待时间。 |

```json
{"workspace":"<当前会话工作区>","action":"models"}
```

```json
{"workspace":"<当前会话工作区>","action":"infer","payload":{"messages":[{"role":"user","content":"检查这批观测的语义标注一致性，列出全部确定的问题与依据：<实际观测>"}]}}
```

只传获准的当前任务材料。图像路径不会自动编码成多模态输入；使用提供方支持的消息
内容及可用图像引用。模型列表不能证明支持图像或某个 reasoning_effort；保留部署默认
推理配置，不猜测服务支持范围。此工具读取完整 JSON，不能传 `stream:true`。

## 结果与限制

同步返回 `service_result`、`evidence_kind: "model_prediction"` 和 `gt_written: false`；
外层结果包含本会话操作与产物目录。读取真实 choices、finish_reason 和实际存在的 usage，
不能把 HTTP 成功当作语义标注正确。标注预测不构成 GT，也不等于生产阶段验收通过。

尽可能一次提交一组相关问题，保留来源和未确定项；出现格式错误先据实际返回修正请求。
超时不保证远端未运行，避免重复请求产生无用计算。路由缺失、服务不可达或模型能力
不足应报告明确错误，由维护者处理；不要更改服务或重启模型。
