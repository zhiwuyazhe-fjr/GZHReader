# GZHReader

GZHReader 是一个 Windows 本地阅读工作台，用来把微信公众号阅读流整理成可回看、可归档、可继续加工的 Markdown 日报。

这次重构后的产品形态不再依赖 Docker，也不再把 GUI 做成一步步的安装向导。默认体验是：

- 安装一个 `GZHReader`
- 内置本地 `wewe-rss` 组件
- 本地 SQLite 存储
- 首页直接进入日报工作台
- 设置页集中管理服务、LLM、输出和自动运行

## 核心变化

- `wewe-rss` 源码已纳入仓库：`third_party/wewe-rss/`
- 发布时会预构建 bundled `wewe-rss` 运行时，并随安装包一起安装
- 不再把 Docker、WSL、MySQL 作为主路径
- GUI 从“控制台 + 向导”重构为“工作台 + 设置页”
- CLI 改为本地服务语义：
  - `gzhreader service start`
  - `gzhreader service stop`
  - `gzhreader service restart`
  - `gzhreader service status`
  - `gzhreader service logs`
  - `gzhreader service open-admin`

## 仓库结构

```text
src/gzhreader/            # Python 主程序
third_party/wewe-rss/     # vendored 上游源码
scripts/build_wewe_rss.ps1
packaging/                # PyInstaller + Inno Setup
```

## 运行模型

最终产物链路：

```text
微信公众号 -> bundled wewe-rss -> all.atom -> GZHReader -> SQLite -> LLM -> Markdown 日报
```

本地数据默认分离：

- `GZHReader` 数据库：`%APPDATA%/GZHReader/data/gzhreader.db`
- `wewe-rss` 数据库：`%APPDATA%/GZHReader/wewe-rss/data/wewe-rss.db`
- 日志目录：`%APPDATA%/GZHReader/logs/`

两边都用 SQLite，但各自使用独立文件，不共库、不混淆。

## 开发环境

### Python 侧

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -e .[dev]
```

### bundled `wewe-rss` 运行时

开发时如果要真正启动本地公众号服务，需要先构建 vendored `wewe-rss` 运行时：

```powershell
.\scripts\build_wewe_rss.ps1
```

这个脚本会：

- 使用 `third_party/wewe-rss/` 源码
- 切到 SQLite 模式
- 构建 server + web
- 生成可打包的 `build/wewe-rss-runtime/`

需要本机可用的 Node.js 20+ 与 `pnpm`。

## 常用命令

```powershell
gzhreader init
gzhreader app
gzhreader doctor
gzhreader run today
gzhreader run date 2026-03-27
gzhreader service start
gzhreader service stop
gzhreader service restart
gzhreader service status
gzhreader service logs
gzhreader service open-admin
gzhreader schedule install
gzhreader schedule remove
```

## 打包

先构建 bundled `wewe-rss`，再构建桌面程序：

```powershell
.\scripts\build_release.ps1 -SkipInstaller
```

如果要生成安装包：

```powershell
.\scripts\build_release.ps1
```

发布链路会自动：

1. 构建 `wewe-rss` 运行时
2. 打进 PyInstaller 产物
3. 用 Inno Setup 生成安装包

## GUI 设计方向

当前 GUI 设计基线记录在：

- `.impeccable.md`

方向是“纸页编辑室”：

- 面向个人知识用户
- 日报优先
- 安静、克制、编辑感
- 首页不堆高级配置
- 设置页收纳复杂度

## 第三方说明

仓库内 vendored 的 `wewe-rss` 来源与许可证说明见：

- `THIRD_PARTY_NOTICES.md`
- `third_party/wewe-rss/LICENSE`

`wewe-rss` 上游项目使用 MIT 许可证；本仓库保留其版权与许可证信息，并在此基础上做本地集成与打包。
