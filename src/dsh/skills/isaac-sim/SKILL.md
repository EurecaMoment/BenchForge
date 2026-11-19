---
name: isaac-sim
description: 通过 BenchForge 已配置的 Isaac Sim 服务查询能力、导入资产、执行仿真、采集观测和下载真实产物。用于仿真工具调用与失败恢复，不用于服务部署或源码修改。
---

# Isaac Sim

使用 `benchforge_isaac_sim` 调用已配置的服务。完整 SceneProgram 生产任务优先使用 `benchforge_capture` 等生产工具；原生接口用于其在线能力查询、明确的仿真操作和作业观察。原生操作成功不能代替生产任务的场景质量验收与阶段审核。

本 Skill 及其 `references/` 是接口使用文档入口。不要读取 `third_party`、Git、部署目录或仿真器源码来推测功能，不启动另一个仿真进程，不更改服务配置。文档不足时查询下面的实时接口；缺少必要能力或参数合同则报告具体缺口，不猜参数反复试错。

## 先发现，再调用

1. 调用 `benchforge_isaac_sim({"action":"guide"})`。`guide` 返回生命周期说明、在线操作摘要与产品域计数，不要求读取整套目录。
2. 按任务选择操作，再用 `action="guide", operation="<完整能力名>"` 获取其完整范围与参数；按产品功能调用时用 `feature_id` 取得完整功能描述。需要原始列表时用 `capabilities` 或 `catalog`。
3. 以实时返回的 `availability`、`support`、`reason`、`scope` 和 schema 为准。`AVAILABLE` 说明当前可路由/就绪；`PARTIAL` 和 `UNVERIFIED` 需核对范围；`UNAVAILABLE` 与 `UNSUPPORTED` 不能当作可用承诺。仅有 `{"type":"object"}` 的 schema 没有说明字段，不能据此推导支持或拒绝某字段。
4. 在当前会话工作区保存输入文件，按已公开的合同提交。批量检查所需参数和能力，一次修正已知输入问题；不要为绕过质量失败而删掉用户要求。

工作区与访问身份由 Host 自动绑定，模型无需传 `workspace` 或认证 token。原样保留返回的 `source_path`、`asset_id`、`relative_path`、`job_id` 和 `session_id`，不要凭记忆重写路径或标识。

SceneProgram 可以直接使用本会话上传的原生资产：对象设为 `kind="native"`，`native_asset` 填入上传返回的 `asset_id`、`relative_path`，以及来源资产的 `size`、`source_up_axis`，按需声明 `fix_base`。不要修改源码中的原生目录、部署配置或服务器全局资产目录。目录中的 `configured` 仅说明有绑定，不代表资源存在、渲染成功或机器人能运动；`unconfigured` 不能当作可直接使用。普通 `mesh_import` 接受 GLB/glTF/OBJ/PLY/STL 静态网格，不能通过转换 USD 来保证关节语义。保留机器人原生表示后，仍须用仿真器实际回执验证关节、控制和采集能力。

## 观察到完成，验证产物

`submit` / `invoke` 返回受理作业。保存返回的 `job.job_id` 和 `receipt_path`，对同一作业调用 `status` 或最长 60 秒的 `wait`。`QUEUED`、`RUNNING`、`accepted`、观察超时都不等于完成；等待超时不重提作业、不自动取消。

只有 `job.state="SUCCEEDED"` 才下载该作业清单中的产物。使用下载返回的 `files` / `artifacts` 检查真实内容并展示给用户；图像、深度、物理回执或轨迹是否符合要求还需独立验收。`FAILED`、`CANCELLED`、`INTERRUPTED` 要报告真实状态和原因，不能生成替代产物冒充成功。

提交响应丢失时复用原 `request_id` 和完全相同的请求。参数改变必须使用新身份。`request_id` 放在工具参数顶层，不放进 `request`。停止观察不等于取消远端工作；`cancel` 是否成功以服务确认的状态为准。

## 选对操作族

能力名、提交操作名、产品功能 ID 是三种不同标识。`extension.asset.import` 对应提交中的 `asset.import`；`invoke.feature_id` 必须从目录获取，不把能力名直接填过去。

同一服务可能同时提供标准场景与 `extended.` 扩展族。它们的同名后缀不代表相同参数或共享会话；`session_id` 必须回到创建它的操作族。不能把标准场景 ID 交给扩展机器人接口，或在失败后只换前缀重试。并发提交也不证明仿真器并行执行，多相机数量不证明渲染同时进行。

## 按需读取

- 首次提交、上传/下载或恢复作业时，读取 [工具参数与完整调用示例](references/tool-api.md)。
- 使用持久场景的导入、步进、多相机采集或处理同名扩展接口时，读取 [场景操作与会话边界](references/scene-operations.md)。

这些引用随 Skill 提供；运行时仍以实时发现和真实作业结果为准。
