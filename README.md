<p align="center">
  <img src="desktop/public/gzhreader-logo.svg" width="104" height="104" alt="GZHReader Logo">
</p>

<h1 align="center">GZHReader</h1>

<p align="center">
  本地运行的公众号阅读与每日简报工作台
</p>

<p align="center">
  <a href="https://github.com/zhiwuyazhe-fjr/GZHReader/releases"><img alt="Windows 10/11" src="https://img.shields.io/badge/Windows-10%20%2F%2011-2F5B4E?style=flat-square"></a>
  <img alt="Local first" src="https://img.shields.io/badge/数据-本地保存-2F5B4E?style=flat-square">
  <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/License-MIT-C88A43?style=flat-square"></a>
</p>

<p align="center">
  粘贴一篇公众号文章链接，关注更新、阅读摘要和每日简报都会集中到同一个桌面工作台。
</p>

---

## GZHReader 能做什么

GZHReader 面向希望稳定跟进公众号内容的普通用户。它在 Windows 本地运行，不需要 Docker，也不会启动供用户访问的网页服务。

- **通过文章链接关注公众号**  无需记忆公众号 ID，也不依赖关键词搜索
- **自动获取最新文章**  首次同步近 30 天内最多 20 篇，之后按设定频率更新
- **集中阅读与搜索**  支持未读状态、公众号筛选和本地全文搜索
- **整理文章重点**  生成内容摘要、关键要点、主题标签和一句话结论
- **生成每日简报**  按用户设定的时间汇总当天内容，并保存为 Markdown
- **托盘持续运行**  关闭主窗口后仍可自动刷新和发送聚合通知
- **本地保存数据**  文章、摘要、设置和简报都保存在用户电脑中

## 使用流程

```text
粘贴公众号文章链接
        ↓
确认识别出的公众号
        ↓
首次扫码连接微信读书
        ↓
同步文章并整理重点
        ↓
接收新文章提醒与每日简报
```

1. 安装并打开 GZHReader。
2. 在“公众号”页面粘贴任意一篇目标公众号文章链接。
3. 确认识别结果并开始关注。
4. 首次使用时按提示在 Edge 中完成微信读书扫码。
5. 返回工作台，等待文章同步和内容整理。
6. 在设置中选择刷新频率和每日简报时间。

微信可能在登录或更新过程中要求再次扫码或完成人机验证。遇到提示时，请在打开的浏览器窗口中完成操作，不要连续反复重试。

人机验证产生的 `ticket`/`randstr` 是临时的一次性授权。GZHReader 只使用浏览器已经成功返回的结果，不会在后台重放该票据；授权失效时自动刷新会暂停，并提示用户在浏览器中重新确认，以避免连续触发验证或风控。

## 工作台

左侧只保留四个主要入口，首页用于快速确认连接状态、新文章和今日简报。

| 页面 | 用途 |
| --- | --- |
| 首页 | 查看今日新增、刷新状态、最新文章和今日简报 |
| 公众号 | 添加、暂停、刷新或移除关注的公众号 |
| 全部文章 | 搜索、筛选、标记已读并查看文章详情 |
| 每日简报 | 查看、重新生成或打开本地 Markdown 简报 |

设置页只呈现普通用户需要理解的选项，包括摘要服务、刷新频率、简报时间和应用偏好。

## 安装

当前支持 **Windows 10/11 x64**。

1. 前往 [Releases](https://github.com/zhiwuyazhe-fjr/GZHReader/releases) 下载最新安装包。
2. 运行 `GZHReader_*_x64-setup.exe` 完成安装。
3. 首次启动后按工作台提示添加公众号并连接微信读书。

安装包已经包含运行所需的桌面端和本地核心。用户无需另外安装 Python、Node.js、数据库或 Docker。

## 内容摘要

GZHReader 支持 OpenAI 兼容接口。用户只需要填写以下三项。

- 服务地址
- API Key
- 模型名称

摘要服务不可用时不会中断文章采集。应用会先保留文章和临时摘要，连接恢复后可以在文章详情中重新整理。

## 刷新与每日简报

自动刷新支持以下选项。

- 每 15 分钟
- 每 30 分钟
- 每 1 小时
- 每 2 小时
- 每 4 小时
- 仅手动刷新

默认每 1 小时刷新一次。每日简报默认在 21:30 生成，用户可以精确到分钟修改时间。电脑休眠错过生成时间后，应用会在恢复运行时补生成当天简报。

## 本地数据与隐私

应用数据默认保存在以下独立目录。

```text
%LOCALAPPDATA%\GZHReader\workspace-v3\
  gzhreader.db
  backups\
  browser\
  logs\
  secrets\
```

每日简报保存在用户文档目录。

```text
%USERPROFILE%\Documents\GZHReader\Briefings\YYYY-MM-DD.md
```

- SQLite 启用 WAL、外键、FTS5 全文搜索和自动备份
- 微信读书登录凭据与摘要服务密钥使用 Windows DPAPI 加密
- 凭据不会以明文写入 SQLite
- 新版使用独立数据目录，不迁移也不自动删除旧版数据

文章内容会按功能需要请求微信读书和微信公众号页面。启用内容摘要后，文章文本会发送到用户自行配置的摘要服务。

## 技术架构

```text
Tauri 2 + Vue 3 桌面工作台
            ↕
     JSON-RPC 2.0
            ↕
GZHReader Python 本地核心
            ↓
       微信读书 Web
            ↓
SQLite + 内容摘要 + 每日简报
```

- 桌面端使用 Tauri 2、Vue 3、TypeScript、Vite 和 Pinia
- Python Core 由 PyInstaller 构建为内部 Sidecar
- 桌面端与本地核心通过 stdin/stdout 通信
- 不开放 localhost HTTP 端口
- 不依赖 wewe-rss、远程 Bridge、RSS 中转服务或 bundled Node runtime

## 从源码开发

### 环境要求

- Windows 10/11 x64
- Python 3.11+
- Node.js 20+
- Rust stable
- Visual Studio Build Tools 2022
- MSVC C++ 工具链和 Windows 10/11 SDK

### 安装依赖

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,build]"

cd desktop
npm ci
```

### 运行测试

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m pytest

cd desktop
npm run build
```

### 构建本地核心

```powershell
.\scripts\build_sidecar.ps1
```

生成文件位于

```text
desktop\src-tauri\binaries\gzhreader-core-x86_64-pc-windows-msvc.exe
```

### 构建桌面安装包

Tauri 更新包必须签名。签名私钥应保存在仓库外。

```powershell
$env:TAURI_SIGNING_PRIVATE_KEY_PATH = "$env:USERPROFILE\.tauri\gzhreader-updater.key"
.\scripts\build_desktop.ps1
```

## 项目目录

```text
src/gzhreader_core/       Python 本地核心
desktop/                  Vue 与 Tauri 桌面应用
scripts/                  Sidecar 和桌面构建脚本
tests/                    Python Core 测试
third_party/licenses/     第三方许可证副本
```

## 第三方代码

微信读书文章解析、分页追赶、节流和错误处理参考并改写自 [rachelos/we-mp-rss](https://github.com/rachelos/we-mp-rss) 的 MIT 许可实现。GZHReader 不直接导入或运行该仓库，详细说明见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 已知限制

微信读书 Web 接口并非稳定的公开 API，后续可能发生变化。GZHReader 已实现认证失效停止请求、一次性授权不重放、网络退避、风控冷却和失败不推进同步进度，但仍需要随微信侧变化持续维护。访问授权过期后需要用户在浏览器中重新确认，无法保证完全无人值守更新。

## License

[MIT](LICENSE)

<p align="center">
  <strong>GZHReader</strong><br>
  把每天值得读的内容，安静地整理在本地。
</p>
