# 部署配置

仓库的应用部署配置统一放在 `config/`。从 `benchforge.example.json` 初始化 `benchforge.local.json`，服务地址、模型、数据库及 Isaac Sim 端点都从这一个私有配置生成。

| 文件或目录 | 用途 |
| --- | --- |
| `benchforge.example.json` | 可提交的默认配置模板，不含真实凭据 |
| `benchforge.local.json` | 当前实例的私有设置，由 `python benchforge_app.py init` 创建 |
| `generated/` | `sync` 生成的服务端、客户端、DSH 与数据库配置，不手工复制到另一实例 |
| `dsh.env` | 当前实例的私有环境变量与凭据，不提交仓库 |
| `compose.yml` | 托管 PostgreSQL 的 Compose 声明，凭据由生成的私有环境提供 |

显式传入配置路径仍受支持；同机多实例可以使用各自部署目录下的 `config/`。相对配置路径以引用的服务配置所在目录为基准。Python 服务及客户端共用 `service_access/service_config.py`，DSH 的语言入口通过共同配置样例核对行为。

DSH bundle 必需的 `src/dsh/cordis.patch.yml` 属于插件声明；构建配置、协议 schema、生产配方和测试样例保留在各自模块，不混入部署设置。
