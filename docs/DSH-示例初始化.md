# DSH 示例初始化

仓库部署通过 `benchforge-examples` Host 插件导入公开的 SpatialForge 对话。
生成器与旧 profile 升级入口都安装该插件；普通 Client 不持有导入逻辑。

| 文件 | 职责 |
| --- | --- |
| `example/SpatialForge/bootstrap.json` | 稳定身份、归档路径、可迁移的旧路径根、实际交付的产物 |
| `src/dsh/examples/archive.mjs` | 完整读取多帧 Zstandard 归档，调用 DSH 校验器，迁移路径并追加当前预设与权限 |
| `src/dsh/examples/bootstrap.mjs` | 安装独立工作区，通过持久化句柄导入、恢复中断的初始化并登记工作区 |
| `src/dsh/examples/host.mjs` | 声明 Host 依赖、配置、领域导入回执与资源清理 |

遵循《DSH-预设能力与编写规范》的 Host、会话与业务存储边界：

- 使用 `sessionPersistence.create/open/append/flush/close`，不猜测或直接写入 DSH 会话目录。
- 使用 `workspaceRegistry.create/attachSession` 建立经过会话头校验的工作区归属。
- 归档完整落盘后，使用 `sessionProjectionCache.coldSnapshot` 发布侧边栏标题与非空摘要；无需先打开会话或启动 Agent。已有导入回执只补一次摘要，不改对话历史。
- `storageDomain` 保存一次性导入回执；完成后不反复读取大归档，不重复导入，也不恢复用户删除的示例。
- 仅导入外部归档副本。原始归档不修改，目标会话已有的提交历史不覆盖；失败重试只补全经一致性核验的前缀。
- 导入前校验完整归档与清单文件。文件仅进入本实例的独立示例目录，不覆盖用户现有目录。
- 最后追加当前 `benchforge` 预设、当前工作目录与 `workspace-write`/`ask` 权限，避免沿用旧机器的完全权限。
- 通过标准 `deliverables/presented` 事件声明实际复制的示例产物，现有产物投影与 Remote 负责展示。
- 不创建运行中的 Agent，不重放工具副作用，不把示例导入当成新的生产或验收成功。

回归入口：`node --test src/dsh/examples/*.test.mjs` 与 `python -m unittest discover -s tests -p test_dsh_presets.py`。
部署验收需使用真实 JSONL 持久化后端：新实例列出非空示例、会话可打开、产物能读取，重启仍只保留一份。
