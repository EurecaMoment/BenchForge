# 下载后快速使用

## 完整 DSH 应用入口

准备 Python 3.10+、Git、Node.js 22.19+ 或 24+：

```bash
git clone https://github.com/EurecaMoment/BenchForge.git
cd BenchForge
python benchforge.py setup
python benchforge.py start
```

安装器自动取得固定版本的 DSH 源码作为第三方依赖，首次安装构建一次，后续直接启动。界面中配置模型提供方，并在新会话选择 **BenchForge**。DSH 主程序负责标准交互、读图、PTC、子智能体和后台任务，本仓库负责 benchmark 专项能力。

使用项目内独立 DSH home 与模式 overlay，不更改已有 DSH、官方预设或 SpatialForge。已有编译好的 DSH 可用 `python benchforge.py setup --dsh-root 路径` 复用，省去编译。首次从源码构建需要下载依赖，不能保证在任意网络下很快；可先用 `python benchforge.py demo` 无网络验证核心流程。

## 1. 不装模型，先确认能运行

需要 Python 3.10 或更新版本。在解压后的仓库目录运行：

```bash
python quickstart.py
```

无需注册 API、安装 DSH、安装 GPU 库或下载模型。这会执行“程序生成源数据 → 证据编译 → 题包构建 → 预测评分”，在 `runs/quickstart` 留下题包和结果。两道示例题中故意答错一道，得分应为 0.5。

这只是验证安装和数据链路的小示例，不是仿真或正式 benchmark 质量展示。

## 2. 在已有 DSH 中增加新模式

```bash
python quickstart.py --dsh-root /path/to/deepseek-harness --profile /path/to/profile/cordis.patch.yml
```

脚本创建本项目的 `.venv`，安装 Python 依赖，生成 **BenchForge** 模式，并备份后更新指定 profile 中属于它自己的条目。它不会修改官方四模式、SpatialForge 或默认模式，不切换正在运行的会话。

不确定 profile 路径时先省略 `--profile`，脚本只生成 `benchforge.local.yml`，不会更改 DSH 配置。Windows 同样支持，路径有空格时加引号。

在新会话选择 **BenchForge**，可先输入：

> 帮我用本地已有数据构建一个小型空间问答 benchmark，先做几道样例给我看。

模型保留标准模式的文件操作、读图、任务代码与交互，并能选择 PTC 和子智能体执行独立工作。具体标注、仿真和构建操作会以独立工具出现。

## 3. 只配置你需要的能力

| 需求 | 需要准备 |
|---|---|
| 已有标签数据 → benchmark | Python；数据文件和图片 |
| SAM3 分割 | 已运行的兼容 SAM3 服务；配置服务 URL |
| YOLOE 检测 | 已运行的兼容 YOLOE 服务；配置服务 URL |
| Depth Anything 3 | 已运行的 DA3 backend；配置 URL |
| 本地 VLM 标注建议 | OpenAI-compatible 非流式 chat 服务；URL 和环境变量 token |
| Habitat / LIBERO | 对应 SDK 环境、场景或演示数据；配置 Python 命令 |
| CARLA | 对应 Python SDK 环境和已运行的 CARLA server |
| Isaac | 安装 Isaac 的机器，配置其 Python launcher |

服务协议与原 BenchClaw 服务一致，并不意味着任意同名第三方 HTTP 服务都兼容。第一次调用前通过 `catalog` 查看示例和接口字段。

独立 Isaac 采集器支持原生盒体、相机、深度、实例标签和位姿记录；不包含 SpatialForge 的资产重建和桌面队列。服务器到另一台桌面的远程分发需要另外配置执行器，当前版本不会冒充已经内置该能力。

## Docker 可选

```bash
docker build -t benchforge .
docker run --rm -v "${PWD}/runs:/data" benchforge
```

镜像只包含轻量核心和示例，不打包多个 GPU 框架。标注服务运行在镜像之外，模拟器按各自环境运行。

## 发布文件

提交源码、示例、文档和 CI 即可。`.gitignore` 已排除 `.venv`、`runs`、`config.local.json`、生成的本机模式配置和环境变量文件。不要提交模型权重、私有图片、API token 或仿真数据目录。
