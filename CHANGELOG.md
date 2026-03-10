# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
