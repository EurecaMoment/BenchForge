# 工具参数与调用示例

本文件说明 `benchforge_isaac_sim` 的使用合同。Host 自动绑定当前会话的工作区与访问身份，模型无需填写 `workspace` 或认证 token；下例的尖括号占位符仅替换为工具真实返回值。

## 动作

| `action` | 额外参数 | 返回或限制 |
| --- | --- | --- |
| `guide` | 可选 `operation` 或 `feature_id` | 说明和实时摘要；`operation` 选中完整能力，`feature_id` 选中完整产品功能；名称不存在会报错 |
| `capabilities` | 无 | `result.capabilities` 包含操作、可用性、范围和公开 schema |
| `catalog` | 可选 `feature_id` | 产品域/功能及 `support`、`availability`、`request_schema`、`fixed_arguments`；过滤后为 `result.features` |
| `assets` | 无 | 服务已上传资产的标识及相对文件名，不提供任意服务端文件浏览 |
| `upload` | `source_path`；可选 `asset_id`、`relative_path` | 来源必须是当前工作区内已保存文件；返回 `asset` 中的标识与相对路径 |
| `submit` | `request`；可选 `request_id` | `request` 是一个原生任务消息；返回 `job`、`receipt_path`，受理不代表完成 |
| `invoke` | `feature_id`、`request`；可选 `request_id` | `request` 直接是该功能的参数，不再套 `extension`；返回持久作业 |
| `status` | `job_id` | `job` 的状态、结果、失败原因和产物清单 |
| `wait` | `job_id`；可选 `timeout_seconds` | 观察时间大于 0、至多 60 秒，默认 30 秒；超时仍返回同一作业，不重新提交 |
| `cancel` | `job_id` | 服务确认是否取消；已运行的 GPU 作业可能拒绝取消 |
| `download` | `job_id` | 仅接受成功作业，下载清单文件至当前工作区；返回 `files`、`artifacts` 和回执 |

`operation` 只用于 `guide` 过滤，不用于代替 `submit.request.extension.operation`。未指定 `request_id` 时，DSH 以会话与工具调用身份产生稳定提交身份；需显式恢复一次不确定提交时保留原身份和请求，不创建另一份相同工作。

## 能力与产品入口

```json
{"action":"guide"}
```

下面读取的是能力名。如果在线列表没有它，选择任务所需的实际能力，不能臆造名称：

```json
{"action":"guide","operation":"extension.asset.import"}
```

产品目录先通过 `catalog` 获取；`feature_id` 是该目录的真实 ID：

```json
{"action":"guide","feature_id":"<目录返回的功能ID>"}
```

`guide` 的默认 `result.live.operations` 是摘要；选中 `operation` 后才包含对应能力的完整 `details.parameter_schema`。选中 `feature_id` 后，`result.live.features` 包含完整功能。细节不在摘要中不等于功能缺失。

## 上传、提交、观察、下载

先在当前工作区准备真实 USD 文件，并核对服务支持下述导入操作。

```json
{"action":"upload","source_path":"tasks/example/work/scene.usda"}
```

使用上传返回的 `asset.asset_id` / `asset.relative_path`：

```json
{
  "action":"submit",
  "request_id":"<为本次导入保存的稳定身份>",
  "request":{
    "extension":{
      "operation":"asset.import",
      "arguments":{
        "source":{"asset_id":"<asset.asset_id>","relative_path":"<asset.relative_path>"},
        "path":"/World/Scene"
      }
    }
  }
}
```

`submit.request` 只选择一个原生任务字段。例如原生 `snapshot` 任务用 `{"snapshot":{}}`；扩展任务用上述 `extension` 结构。具体任务及字段均以在线合同为准。

```json
{"action":"wait","job_id":"<job.job_id>","timeout_seconds":30}
```

只有成功导入后，才使用其 `job.result` 中真实返回的场景 `session_id` 进行后续操作。不同操作的结果形状可能不同，不猜嵌套路径。

产生图像或导出文件的作业成功后下载：

```json
{"action":"download","job_id":"<成功采集或导出作业的job_id>"}
```

下载只能访问该作业清单中的产物。后续读取和展示使用返回的 `source_path` 与 `artifacts`，不要拼接远端文件系统路径。`operation_id` 是一次 BenchForge 工具调用，`job_id` 是原生作业，`session_id` 是仿真场景；三者不能互换。

## 产品功能调用的结构

```json
{
  "action":"invoke",
  "feature_id":"<目录返回的功能ID>",
  "request_id":"<为这组参数保存的稳定身份>",
  "request":{}
}
```

上例仅展示外层结构。按该功能的 `request_schema` 填写 `request`，不要直接提交空对象，也不要再包 `arguments` 或 `extension`。`fixed_arguments` 是目录限定的操作映射，不是任意覆写入口。

## 失败恢复

| 情况 | 处理 |
| --- | --- |
| 提交响应丢失、是否受理未知 | 保留相同 `request_id` 和完全相同参数恢复；不换身份重复提交 |
| `wait` 返回仍运行 | 记录进度，继续观察同一作业；不要把超时写成作业失败 |
| 参数被拒绝 | 对照完整实时 schema 与具体错误，批量修复已知输入问题；修改后的请求用新身份 |
| schema 不足或能力不可用 | 保存准确接口/缺失字段/错误回执并报告，不读源码猜补或重复试同一错误 |
| 作业成功但质量失败 | 按真实产物修复输入并重新执行必要阶段；成功状态不替代场景验收 |
| 想停止远端作业 | 调用 `cancel` 并检查返回状态；断开观察不能证明已停止 |
| 作业归属校验失败 | 确认当前会话与返回的作业 ID；不能借用另一会话的回执绕过校验 |
