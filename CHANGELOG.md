# Changelog

## Unreleased

### Fixed

- 不再拦截人机验证页的正常关闭，使验证成功回调可以回到公众号阅读页。
- 将临时访问授权失效与登录失效分开处理；浏览器成功请求使用过的一次性授权不再被后台重放，并保留现有登录会话。
- 风控冷却期间保留本地会话，并在工作台显示准确的验证或冷却状态。
- 人机验证改用本机 Edge/Chrome 的常规启动参数，再通过仅限本机的调试端口连接，避免 Playwright 默认的自动化浏览器参数持续触发风控。
- 修复重新验证后的提示文字显示为问号。
- 公众号名称优先读取文章页权威作者节点，并按公众号标识匹配资料卡；不再把小程序属性、其他公众号资料卡或整段脚本误识别为名称和标题。

## [3.0.0] - 2026-08-05

### Added

- 新增 Tauri 2 + Vue 3 Windows 本地桌面工作台。
- 新增 Python JSON-RPC Sidecar，不监听本地端口。
- 新增通过公众号文章链接识别和关注公众号的流程。
- 新增可见 Edge/Chrome 微信读书扫码连接与凭据自动捕获。
- 新增微信读书文章列表分页、正文获取、节流、退避和认证失效处理。
- 新增 SQLite WAL、FTS5、本地未读状态、同步任务和每日自动备份。
- 新增文章摘要、关键要点、标签、一句话结论和每日简报。
- 新增用户可配置的刷新频率和每日简报时间。
- 新增托盘常驻、聚合通知、开机启动和 Tauri 签名更新配置。

### Changed

- 首次同步限制为近 30 天、最多 20 篇文章。
- 摘要服务仅保留 OpenAI 兼容接口。
- Timeout、Retries、Temperature 固定在代码内部，不再向普通用户显示。
- 新版数据使用 `%LOCALAPPDATA%\GZHReader\workspace-v3`，不迁移旧数据库。
- 界面改为克制的本地阅读工具风格，不使用渐变、玻璃拟态或 AI 装饰图案。

### Fixed

- 修复公众号误用普通书籍阅读路由导致微信读书 404，改为首页扫码后进入编码的公众号阅读器路由。
- 修复微信文章被重定向到访问验证页时无法识别公众号的问题，现在会自动打开 Edge/Chrome 完成验证并继续识别。
- 补充现代微信文章页的公众号名称、标题和 `biz` 提取兼容。

### Removed

- 移除 FastAPI、Uvicorn、Jinja、HTMX 和网页界面。
- 移除 Typer、Console Entry、CLI 快捷方式和 Windows Task Scheduler。
- 移除 RSS/Atom 主流程、feedparser、wewe-rss 和远程 Bridge。
- 移除 bundled Node runtime、Docker、Compose、MySQL 和旧 Inno Setup 发布链。
- 移除 `weread.111965.xyz`、`PLATFORM_URL` 和 `/feeds/all.atom` 等旧运行路径。

## [2.0.0] - 2026-03-27

- 旧版 bundled RSS 服务版本。该架构已在 3.0.0 中完全移除。
