# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [v1.5.0] - 2026-03-14

### Changed
- 🎨 **控制台界面整体打磨**：收紧左侧运行概况卡片，统一顶部提示区样式，弱化滚动条存在感，让首页信息密度更高、观感更干净。
- 🧭 **Docker 引导页重构**：移除无效左侧栏与冗余提示块，加入更详细的安装说明，重排下载/文档/重新检测按钮，减少首次安装 Docker Desktop 时的困惑。
- 📝 **结果与设置卡片优化**：放大首页关键说明文字，并让“查看结果”“打开目录”“去设置”等操作按钮占满整行，减少误触和视觉断裂。
- 🧰 **帮助信息重新归位**：把主区“先理解两件事”迁移到左栏“帮助中心”，首页首屏更聚焦在真正的操作路径上。

### Added
- 💬 **全新“关于”弹窗**：新增品牌头部、开发动机、反馈入口、开源项目、分享支持和作者信息等完整分区，形成更统一的产品介绍页。
- 🔗 **分享与反馈入口**：支持一键复制仓库链接“分享给朋友”，并提供 GitHub Issues 入口用于问题反馈与后续支持。

### Fixed
- 🧱 **Docker 未就绪状态下的布局噪音**：移除了会干扰判断的默认提示与多余说明，让用户更容易聚焦“先装好并启动 Docker”这件事。

## [1.0.0] - 2026-03-10

### Added
- 🎉 **Initial Release:** Welcome to GZHReader 1.0.0!
- 🖥️ **向导式 Web 控制台**: 8 步可视化引导，提供从 Docker 环境检查到 Windows 计划任务的完整配置流。
- 🤖 **广泛的大模型支持**: 支持任意 OpenAI 兼容接口（如 OpenAI, Azure, DeepSeek, Ollama 等）生成文章精华摘要。
- 🧠 **三重正文提取保障**: RSS 全文解析、基于 readability-lxml 的 HTTP 抓取，以及 Playwright 浏览器渲染层层兜底。
- 📅 **自动化运行**: 内置 Windows 计划任务一键安装与卸载功能。
- 🔒 **本地化部署**: 抓取、存储（SQLite）和转换全流程都在本地进行，保障数据隐私。
- ♻️ **智能去重引擎**: 基于 URL 和内容摘要的双重去重，确保每日获取新鲜内容。
- 📝 **Markdown 格式产物**: 按照公众号订阅分组，自动生成对 Obsidian/Notion 等友好的高质量 Markdown 简报。
- 📦 **开箱即用打包方案**: 提供基于 PyInstaller 和 Inno Setup 的双模安装包，支持控制台版（带调试信息）和静默版。
