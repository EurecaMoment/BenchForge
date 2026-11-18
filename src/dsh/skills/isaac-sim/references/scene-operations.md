# 场景操作与会话边界

这些说明用于理解已公开的持久场景操作。它们不是固定可用性清单：先查询 `guide` 的完整能力或产品功能，实际字段和可用性以当前服务为准。

## 三种名称与两类会话

| 标识位置 | 标准场景示例 | 扩展场景示例 |
| --- | --- | --- |
| 能力名 / `guide.operation` | `extension.asset.import` | `extension.extended.asset.import` |
| `submit.request.extension.operation` | `asset.import` | `extended.asset.import` |
| 产品目录 ID / `invoke.feature_id` | 目录可能返回 `sessions.asset.import` | 目录可能返回 `sessions.extended.asset.import` |

产品 ID 必须取自实时目录。只有能力名的 `extension.` 前缀在原生提交时移除；其后的 `extended.` 是操作命名空间，不能随意删除。

不同操作族创建独立会话；不要将标准 `session_id` 交给扩展族，也不要用扩展族的机器人、传感器参数调用同名标准场景操作。原有多机器人或多世界能力是否可用，需单独查询相关操作的 schema、范围与结果；标准刚体采集成功不证明这些能力也成功。

## 标准持久场景的常见顺序

上传真实 USD 及所需依赖 → `asset.import` → 可选 `scene.edit` → `physics.step` → `world.observe` / `scene.inspect` → `scene.capture` / `scene.export` → 下载并验收 → 不再使用时 `scene.close`。

每次修改场景后重新完成步进，才能读取与当前场景相符的物理观测或采集。各变更操作等待成功后再执行依赖它的下一步；保存场景会话与相应作业回执。

| 操作 | 关键输入与边界 |
| --- | --- |
| `asset.import` | 上传句柄 `source={asset_id,relative_path}`；可选 `path`、`session_id`、`fix_base`、`mass_kg`、`physics`、`source_up_axis`。标准场景接收 USD 系列，不由此推断支持 URDF/MJCF/GLB |
| `scene.edit` | `session_id`、`changes`；有界变换或语义标签修改，不是任意 USD 编辑 |
| `physics.step` | `session_id`、`steps`、`dt`；按实时范围设置步长与步数，保存测得的物理回执 |
| `world.observe` | `session_id`、可选 `bodies`；标准实现仅接受 `imu=false`、`contacts=false`，没有 `robots` 参数 |
| `scene.inspect` | `session_id`、`limit`、`cursor`；标准实现无 `path` 过滤。按 `has_more` 和原样返回的 `next_cursor` 翻页 |
| `scene.capture` | `session_id`、已定义的 `cameras`、`size`、`spp`、`annotations`；当前标准合同每批最多 32 个机位。更多机位分批采集，不静默减少输入 |
| `scene.export` | `session_id`、`format=usd|usdz`；标准实现没有 `zip` 格式，文件通过作业清单下载 |
| `scene.close` | `session_id`、可选 `delete=false`；关闭保留历史产物，不等于删除文件 |

导入不传 `fix_base` 时保留已编写的 USD 引用；显式传入时才执行资产归一与碰撞配置，`false` 还创建动态刚体。`mass_kg` 仅对该动态刚体生效。`physics` 的摩擦与恢复系数影响接触；`collider` 与 `mass_source` 只记录调用方元数据，不改变碰撞器选择或质量。参数被接受不代表所有元数据都会改变仿真行为。

## 多相机、并发与验收

按实时合同拆分相机批次；同一次物理状态下采集的多个视角不能冒充多个轨迹时刻。并发提交、可同时观察多个作业和仿真器实际并行执行是不同能力。标准持久场景实现会排队执行 Kit 操作、依次渲染相机，不能仅根据接口返回快就声称 GPU 并行。

下载后检查图像、深度/标签、相机标定、物理状态与用户要求；保留真实失败和缺失项。`scene.capture` / `scene.export` 产物可作为场景证据，不能自动宣称完成轨迹采集；`world.collect` 也需检查真实输出与采集合同。模型预测的标签不替代仿真真值。
