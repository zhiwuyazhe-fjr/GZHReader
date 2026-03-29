# GZHReader

<p align="center">
  <img src="src/gzhreader/static/brand/gzhreader-icon.svg" alt="GZHReader" width="120" />
</p>

<p align="center">
  <strong>把公众号阅读整理成每天可回看的本地日报</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Windows-blue?style=flat-square" alt="Windows supported" />
  <img src="https://img.shields.io/badge/Powered%20by-Python%20%7C%20SQLite-2ea44f?style=flat-square" alt="Tech Stack" />
  <img src="https://img.shields.io/badge/Output-Markdown-lightgrey?style=flat-square" alt="Markdown" />
  <img src="https://img.shields.io/badge/Version-v2.0.0-111827?style=flat-square" alt="v2.0.0" />
</p>

<p align="center">
  <a href="https://github.com/zhiwuyazhe-fjr/GZHReader/issues">问题反馈</a> ·
  <a href="THIRD_PARTY_NOTICES.md">第三方说明</a>
</p>

---

## 💡 简介

**GZHReader** 是一个面向普通用户的**本地公众号阅读工作台**。

它能将公众号内容自动抓取至本地，整理成适合深度阅读与回看的 Markdown 日报。不再让你的阅读节奏被各种应用的消息红点所牵引，回归专注与沉浸。

💡 **开箱即用，无需折腾**：  
只需安装一个主程序，内置公众号独立后台与 SQLite 本地存储，无需搭建复杂环境。

## ✨ 核心特性

- 📥 **本地聚合**：将所有公众号订阅集中到一个本地管理的后台中进行维护，数据掌握在自己手里。
- 🔄 **智能刷新**：每次生成日报前，系统会自动刷新订阅列表，确保获取的内容总是最新。
- 🤖 **AI 摘要支持**：既支持提取公众号内容的**纯整理版日报**，也支持接入 AI 大模型为你生成**智能阅读摘要**。
- 📝 **Markdown 输出**：生成的日报是纯文本的 `.md` 格式文件，极度方便你进行后续编辑，或完美融合进 Obsidian、Notion 等笔记软件进行归档和同步。
- 🎯 **零心智负担**：主路径专为普通用户设计，操作简单直观，不要求掌握任何容器或数据库的操作。

## 🚀 快速开始

1. **打开主程序**：启动 `GZHReader`。
2. **连接账号**：进入内置的公众号后台，扫码连接账号并维护你的订阅列表。
3. **一键生成**：回到工作台主页，点击 **`立即生成今天`**。
4. **查阅日报**：在本地设定好的目录里，即可享受当天的无打扰 Markdown 深度日报。

> **🔔 提示**：如果你暂时还没配置 AI 模型密钥，也没关系。GZHReader 依然会为你完美生成图文并茂的**纯整理版**无摘要日报。

---

## 🛠️ 安装与运行

### 📦 终端用户

当前主目标平台为 **Windows** 系统。

1. 下载并安装最新的 `GZHReader`。
2. 首次打开时，程序会自动在后台准备好本地运行环境和公众号服务。
3. 所有数据默认保存在本机，且**无需**额外安装任何外部数据库应用。

### 💻 开发者使用

如果你希望参与开发或者从源码运行：

**1. 准备环境**

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .[dev]
```

**2. 构建内置服务**

```powershell
.\scripts\build_wewe_rss.ps1
```

**3. 启动主界面**

```powershell
.\.venv\Scripts\python.exe -m gzhreader app
```

<details>
<summary><b>点击查看更多开发常用命令</b></summary>

```powershell
# 修复检查
gzhreader doctor

# 生成今天/指定日期的日报
gzhreader run today
gzhreader run date 2026-03-28

# 服务管理
gzhreader service start
gzhreader service restart
gzhreader service status
gzhreader service open-admin
```

</details>

---

## 🛡️ 关于账号体系

经历最近一轮的重构，我们的账号体系已从“将远端平台代理 token 塞进数据库”全面升级为 **“本地桥接 + 本地会话托管”** 的过渡架构。

这彻底解决了账号体系不稳的痛点：

- **解耦平台**：本地后台不再硬编码依赖可能变更的远端平台代理 URL。
- **杜绝过期数据**：系统不会再带着不可控的过期 token 在后台默默报错。

⚙️ **新机制的工作方式：**

- 旧版数据升级会被拦截，提醒重新连接以保障数据流通畅。
- 本地会话桥安全托管最新登录态。
- 任何行为（刷新、导入、生成）前会执行**可用性预检**。
- 一旦失效，立即停止所有无关的网络请求，明确要求重连，不再“在暗中猜原因”。

> ⚠️ 这并不代表账号“永不过期”，微信平台自身的会话时效仍存在。
> 但这让登录控制权收回了本地：**更早识别、不再误判、杜绝无效生成**。

---

## 📁 核心项目结构

```text
GZHReader/
├── src/gzhreader/              # GZHReader 核心主程序模块
├── third_party/wewe-rss/       # 已接管的内置公众号后台源码 (Vendored)
├── scripts/                    # 各类环境、后端的构建与维护脚本
│   └── build_wewe_rss.ps1 
└── packaging/                  # 包含 Inno Setup 和 PyInstaller 的打包配置
```

---

## ❓ 常见问题 (FAQ)

<details>
<summary><b>Q: 没有配置 AI 密钥，能不能生成日报？</b></summary>
<b>可以。</b> 没有配置 AI 时，程序将会平稳降级，仅为你采集并提取原文生成纯整理版的精美 Markdown 日报。
</details>

<details>
<summary><b>Q: 为什么刷新数据时总是要求重新连接账号？</b></summary>
当系统检测到微信那边的会话事实上已经失效时，会主动拦截并停止所有动作，避免继续使用旧记录污染你的数据库。此时明确地重新连接是防止账号被风控的最佳实践。
</details>

<details>
<summary><b>Q: 我的隐私数据保存在什么地方？会不会上传？</b></summary>
<b>完全在你的电脑上本地存储，绝不回传。</b>
无论是 GZHReader 自身的元数据库、你维护的公众号后台库、Markdown 文件还是系统日志，通通保存在你的硬盘里。不会有外部连接和数据偷跑。
</details>

---

## 📄 第三方声明与开源许可

本项目部分集成了 vendored 的 `wewe-rss` 开源源码，并严格遵循及保留原项目的各项声明和许可证书：

- [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) - GZHReader 集成第三方组件开源说明
- [third_party/wewe-rss/LICENSE](third_party/wewe-rss/LICENSE)

---

<p align="center">
  <strong>GZHReader</strong> v2.0.0 · <em>本地优先 · 阅读整理工作台</em>
</p>