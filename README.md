<div align="center">
  <h1>📰 GZHReader</h1>
  <p><strong>把微信公众号阅读流，整理成一份真正可保存、可回看、可继续加工的日报。</strong></p>

  <p>
    <a href="https://github.com/your-username/GZHReader/releases"><img src="https://img.shields.io/badge/platform-Windows-blue.svg?style=flat-square" alt="Platform"></a>
    <img src="https://img.shields.io/badge/python-3.10+-blue.svg?style=flat-square" alt="Python Version">
    <a href="./LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg?style=flat-square" alt="License"></a>
  </p>
</div>

---

<blockquote style="border-left: 4px solid #f59e0b; padding: 10px 16px; background-color: #fffbeb; color: #b45309; border-radius: 4px; margin: 20px 0;">
  <strong>⚠️ 重要提示：关于 WeWe RSS 端点掉线的说明</strong><br>
由于引用的开源项目 <a href="https://github.com/cooderl/wewe-rss">WeWe RSS</a> 存在已知 Bug，当前在使用过程中<strong>可能需要每次重新登录 / 重新配置</strong>微信扫码才能正常拉取文章。这是一个临时性问题，如果你发现系统提示拉取失败或文章无法更新，请首先前往本地的 wewe-rss 后台重新扫码登录微信。
</blockquote>

---

## ✨ 为什么值得用？

GZHReader 是一个面向 Windows 环境打造的微信公众号日报整理工具。它不仅仅是一个“阅读器”，也不是需要你手工维护繁杂源的工具，而是提供了一条高度自动化的本地工作流：

微信公众号 ➔ wewe-rss ➔ all.atom ➔ GZHReader ➔ SQLite ➔ LLM 总结 ➔ Markdown 日报

项目的正式产品形态为：**GUI 为主，CLI 为辅；完全本地运行，支持打包成便捷的 Windows 安装程序。**

- 🎯 **一站式管理**：用直观的图形控制台串起环境检查、RSS 服务、LLM 配置、结果输出和计划任务。
- 🔌 **零心智订阅**：不需要手工维护一堆 eeds[]，默认只消费一个聚合源 ll.atom。
- 🤖 **智能补全**：遇到 RSS 抓取正文不完整的情况，聪明的补抓逻辑会尝试通过 HTTP 或浏览器拉取正文并交给大模型生成摘要。
- 📝 **Markdown 产物**：纯净的 Markdown 日报结果，非常适合归档、搜索、跨设备同步和二次深度创作。
- 🗄️ **本地存储保障**：核心运行记录和数据严格保存在本地 SQLite，方便重复执行、去重和历史追踪，真正把数据交还给你。

---

## 🚀 快速开始

### 🎒 作为最终用户体验

1. 安装并启动 **Docker Desktop**（必备）。
2. 下载并运行 GZHReader.exe。
3. 在可视化的控制台中一键启动 wewe-rss。
4. 打开 wewe-rss 后台，扫码登录并订阅公众号。
5. 填写你的 **LLM / OpenAI 兼容接口** 配置。
6. 选择一个适合存放 **Markdown 日报** 的输出目录。
7. 点击“立即运行”生成今天的第一份日报，或一键安装为 **Windows 每日计划任务**。

### 💻 从源码运行（面向开发者）

如果你想运行源码或参与共建，可以通过如下步骤：

`powershell
# 1. 建立虚拟环境
python -m venv .venv
.\.venv\Scripts\activate

# 2. 安装依赖并链接项目
pip install -e .

# 3. 启动图形界面应用
gzhreader app
`

*(可选)* 如果你想先生成一套默认配置文件进行排查：
`powershell
gzhreader init
`

### 🛠️ 常用 CLI 命令清单

GZHReader 也为喜欢终端的硬核玩家保留了丰满的命令行支持：

`powershell
gzhreader app                 # 启动图形控制台
gzhreader doctor              # 诊断本地环境健康度
gzhreader run today           # 立即执行一次今日日报生成
gzhreader run date 2026-03-07 # 指定日期生成日报
gzhreader schedule install    # 安装系统计划任务
gzhreader schedule remove     # 卸载系统计划任务
gzhreader wewe-rss init       # 初始化本地 RSS 架构所需环境
gzhreader wewe-rss up         # 启动 RSS 容器
gzhreader wewe-rss down       # 停止 RSS 容器
gzhreader wewe-rss logs       # 查看 RSS 容器日志
`

---

## 🏗️ 架构与数据流

GZHReader 整体运作流程高度解耦：

`mermaid
flowchart LR
    U[用户] --> GUI[gzhreader app 控制台]
    U --> CLI[gzhreader CLI]

    GUI --> S[ReaderService]
    CLI --> S
    
    S --> RSS[RSSClient]
    S --> FETCH[ArticleContentFetcher]
    S --> DB[(GZHReader SQLite)]
    S --> LLM[OpenAI-compatible LLM]
    S --> BRIEF[BriefingBuilder]
    
    RSS --> WR[wewe-rss]
    WR --> MYSQL[(MySQL 可选)]
    
    FETCH --> HTTP[HTTP 抓取]
    FETCH --> BROWSER[本机 Edge/Chrome]
    
    BRIEF --> MD[Markdown 日报]
`

### ⚙️ 一次完整运行的心智模型

1. wewe-rss 将公众号内文转化为标准 RSS / Atom 格式。
2. GZHReader 由 source.url 接口读取聚合源信息，默认为 ll.atom。
3. GZHReader 针对目标日期内的文章进行精准过滤，并存入本地 SQLite。
4. 校验文章正文；如字数不够/缺失，触发 HTTP 或调用浏览器补抓补全。
5. 将干净的长文投喂给 OpenAI 兼容接口，要求以设定好的视角生成要点摘要。
6. 最终的成果落盘为 output/briefings/YYYY-MM-DD.md。

---

## 🔍 "镜像"与"数据库"解惑指南

本项目结合了多个组件。以下是一张让你秒懂的对照表：

| 核心组件 | 是否硬性需求 | 承担的作用 | 普通用户需要关心吗？ |
| --- | --- | --- | --- |
| **Docker Desktop** | ✅ 是 | 在本机提供运行 wewe-rss / MySQL 的基座容器环境 | **要**，必须事先安装并保持运行 |
| **cooderl/wewe-rss:latest** | ✅ 是 | 提供获取微信文章的通道能力，并对外通过 Web 后台展现 | **要**，需要在控制台中启动它 |
| **mysql:8.4** | 仅 compose_variant 为 mysql 时 | 为 wewe-rss 提供存储层 | 发行版默认无需在意 |
| **GZHReader SQLite** | ✅ 是 | GZHReader 专属的心智底座（文章去重、执行日志、生成的摘要） | 后台静默执行，无需手动打理 |
| **Markdown 日报** | ✅ 是 | 系统的最终精华产物，提供绝佳的离线阅读及归档体验 | **最重要**，这就是你的阅读结果 |

### 🔐 几个关键密码释疑

- **AUTH_CODE**：**它仅仅是用来看管本地 wewe-rss 后台的访问门票。** 它绝不是你的微信密码或大模型 API Key。
- **MySQL 密码**：属于系统自建容器通讯用的内部凭证，只要你不需要直接操作容器里的库表，它就是透明的；如果你偏好 SQLite 内核模式（compose_variant = sqlite），则直接连密码都不需要关心了。

---

## 📂 代码结构地图

如果你想修改或者研究源码，这张地图能帮你快速上手：

- **入口层** (cli.py, webapp.py, console_entry.py, gui_entry.py)：程序的启动点和交互包装层。
- **业务编排层** (service.py, riefing.py)：核心脉络，把流程组织成一篇完整的日报。
- **基础能力层** (
ss_client.py, rticle_fetcher.py, summarizer.py, storage.py)：每个小功能（如补抓、与模型对话、存SQLite）。
- **环境隔离层** (
untime_paths.py, scheduler.py, wewe_rss.py)：用来抹平 Windows、打包版环境与开发版之间的差异。

> 📌 **改版本号该去哪？**
唯有此一处：src/gzhreader/__init__.py

`python
__version__ = "0.2.0"
`

修改后通过 .\scripts\build_release.ps1 即能自动驱动发行文件的全域更新。

---

## 📦 发版打包命令

### 纯可执行文件构建
如果你只需提取绿色的 .exe 单文件本体：
`powershell
.\scripts\build_release.ps1 -SkipInstaller
`
生成的目录：dist/GZHReader/

### Windows 安装包构建
需要事先备好 **Inno Setup 6** 才能正常打包。
`powershell
.\scripts\build_release.ps1
`
成品会稳当落在 
elease/ 目录中。

---

## 📜 CHANGELOG 的作用

本项目的 CHANGELOG.md 绝不是流于形式，它是每次更新不可或缺的说明：
- 提醒开发者修补了什么。
- 给终端用户提供了**极其真实的**升级决策依据。
- 在 Github Release 里作为发版文案的第一手材料。

对于所有参与协作的人，请不要忘记在提交新功能前顺手来这里加上一行。

---

## 💡 FAQ

**Q：为什么现在只需要一个 source 聚合源？**
A：通过只接 ll.atom 聚合通道的方法，用户完全摆脱了手动维护大量源列表的苦海。

**Q：既然只有一个源，为什么报表里还能分清不同公众号？**
A：聚合内容本身自带作者和元数据标识，GZHReader 会利用这些标记智能为您归纳同类项。

**Q：我看不到原始 HTML，只有 Markdown 吗？**
A：是的。阅读与归档的本质是剥离噪音。如果你绝对需要保留最初阶的网页，必须前往配置明确开启 output.save_raw_html。

---

## ⚖️ 免责声明

本项目依赖若干第三方服务及外部开源方案（包含但不限于 Docker Desktop、wewe-rss 与 OpenAI 兼容大模型 API），且文章数据皆受其平台使用限制约束。本工具的使用及因此所引发的风险等一概由使用者自理，本库提供的内容不构成最终正式承诺。请确保你的合理使用符合各大平台的合规章程。
