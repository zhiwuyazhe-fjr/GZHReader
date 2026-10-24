# GZHReader 维护审查与重构记录

日期：2026-10-24。审查基于工作区原始提交 `1c04a2d`。提交与同步情况以 Git 历史为准，本轮没有发布或安装桌面应用。

提交前已整合远端 `9cde7a4` 及其前序修复，保留正常浏览器验证、一次性授权防重放、公众号元数据解析和简报去重。对授权已消耗的浏览器首导只消费已验证缓存页，增量仍必须触达旧游标；新增三项组合回归，避免为凑足首次篇数重放授权。

## 结论

项目原有架构可以保留。主要问题集中在失败恢复、旧数据兼容、搜索范围和时间边界，而非需要更换框架。原有 23 项 Python 测试和前端构建能够通过，但没有覆盖这些边界。

本轮已修复下表中的问题，增加有针对性的回归测试，保留 Tauri + Vue + Python stdin/stdout + SQLite 架构。测试、浏览器预览和 EXE 冒烟均使用模拟或隔离数据，没有登录真实微信账户，也没有调用付费摘要服务。

## 已处理发现

P1 表示可能导致启动失败、任务停滞、错误数据或资源失控；P2 表示功能结果不准确、恢复困难或维护流程不可靠。

| 优先级 | 原有问题与影响 | 本轮处理 | 主要位置 |
| --- | --- | --- | --- |
| P1 | 旧库缺字段时先建索引，应用无法启动 | 先补列再建索引，保留已有正文，升级 schema 到 5 | `storage.py` |
| P1 | 首次抓取只读一页，长时间未更新后超过 200 篇无法追赶；畸形响应可能推进游标 | 修正分页终止条件、去重及响应校验；完整追到旧游标后才更新进度 | `providers/weread.py` |
| P1 | 核心每次重启成功都清零计数，连续崩溃可无限重启；写入失败留下悬挂请求 | 按进程代次隔离输出、限制连续恢复次数、统一清理请求与计时器 | `desktop/src/lib/rpc-client.ts` |
| P1 | 创建同步任务失败后锁不释放；重复后台请求假报 accepted；关停后仍可能发新请求 | 启动时原子占锁、终态事件在释放锁后发出、协作取消后续正文与摘要请求 | `service.py`, `rpc.py` |
| P1 | 验证码长页面和跨主机重定向可能落为正文 | 微信来源仅接受最终微信主机上的正文容器；保留通用页面正常重定向能力 | `article_fetcher.py` |
| P2 | 一至两字中文搜索漏结果，改名后索引过时；全部文章只筛最近 200 篇 | 短词转义 LIKE、维护 FTS、后端分页；前端查询全部本地文章，每页 50 篇 | `storage.py`, `useWorkspace.ts` |
| P2 | 用 UTC 字符串日期统计上海文章，凌晨文章可能归到前一天 | 配置时区统一计算日界，按实际时间排序并建立索引；打包包含 tzdata | `storage.py`, `scheduler.py`, `dates.ts` |
| P2 | 短文章被拒绝、混合中文和 JS 转义出现乱码、登录缓存可能串公众号 | 正文容器判定、逐个 JS 转义、认证只接收目标公众号第一页 | `article_fetcher.py`, `resolver.py`, `browser/auth.py` |
| P2 | 关闭过自动摘要或曾经失败的文章不再整理；单篇摘要错误拖累整个来源 | 独立补正文/补摘要队列，分别限制每轮 20 次；单篇失败保留重试机会 | `service.py`, `storage.py` |
| P2 | 部分 AI 设置更新清空其他配置，错误 JSON 被标成功，永久 4xx 重复请求 | 合并隐藏配置、校验 URL 和返回结构、永久错误停止重复尝试 | `ai.py` |
| P2 | 长简报摘要输入被截断，直接覆盖文件失败时可能丢旧版本；历史重生成日期不正确 | 按字符预算分层汇总，串行生成、临时文件原子替换，使用所选日期 | `briefing.py`, `useWorkspace.ts` |
| P2 | 后台刷新覆盖未保存设置，忙碌状态 1.5 秒后强制清除，旧搜索覆盖新搜索 | 草稿与服务数据分离，使用实际同步终态，按请求版本忽略迟到结果，清理事件与计时器 | `useWorkspace.ts` |
| P2 | 标准机器找不到固定 D 盘 VS 路径，npm ci 失败仍继续，签名路径未正确传递 | vswhere 发现工具链、检查退出码、恢复签名环境；版本/tag 一致性检查 | `scripts/build_*.ps1` |

另修复每天启动覆盖当天数据库备份、损坏凭据 JSON 的健壮性、RPC 参数类型和通知语义、简报重试退避，以及刷新期间修改设置被覆盖的问题。

## 重构边界

- `App.vue` 保留页面结构；业务状态、查询和事件清理集中到 `composables/useWorkspace.ts`。
- `ArticleDrawer.vue` 管理阅读内容、Escape、Tab 焦点范围与关闭后的焦点恢复。
- `lib/core.ts` 只选择运行环境和建立 Tauri 通道；`rpc-client.ts` 负责进程和请求生命周期；`preview.ts` 仅提供浏览器演示数据。
- `types.ts` 定义界面模型；`dates.ts` 负责工作区日界与时间显示。
- 移除未使用的 Pinia、Arco、HelloWorld 与对应 SVG 模板资源。遗留 PNG 和编译对象的删除被自动审批拦截，未删除。
- 沿用 `.impeccable.md` 的安静、纸面阅读方向。使用 `awesome-frontend-workflow`、`frontend-design`、`harden`、`audit`、`polish` 检查既有界面；本轮没有引入 React Bits、额外动效库或新视觉资产。

## 依赖与构建

npm 官方注册表审计曾报告 Vue 的 server-renderer、markdown-it、nanoid、source-map-js 共 5 个依赖条目有公告。项目不使用 SSR，因此该项依赖公告不能直接解释为本桌面应用存在可利用的 SSR 漏洞。Markdown 解析路径则用于每日简报，已更新修复版本。

本轮仅更新声明范围内的兼容版本，没有使用 `--force`：最终锁定 Vue 3.5.44、markdown-it 15.0.3、nanoid 3.3.20、source-map-js 1.2.2、Vite 8.3.5。之后重新执行 `npm ci` 验证锁文件，官方注册表完整 npm 审计结果为 **0 项已知漏洞**。默认 npm 镜像不支持 audit API，因此审计命令显式使用官方注册表，没有修改用户的全局 npm 配置。

- [markdown-it 解析性能公告及 15.0.3 修复版本](https://github.com/advisories/GHSA-gfr2-rv44-r23c)
- [Vue server-renderer 公告及 3.5.42 修复版本](https://github.com/advisories/GHSA-g2v6-rqmx-r4w6)
- [Python 官方时区数据说明：Windows 应声明 tzdata 依赖](https://docs.python.org/3/library/zoneinfo.html#data-sources)

开发环境要求更新为 Node.js 22.19+（内置 TypeScript 测试执行）及 Rust 1.89+（当前锁定依赖的真实最低要求）。新增日常 push / PR CI，发布流程也执行回归和版本/tag 校验。

## 验证证据

| 检查 | 结果 |
| --- | --- |
| 原始 Python 基线 | 23 项通过 |
| 审查阶段 Python 全量 | 108 项通过，无警告 |
| 整合远端后的 Python 全量 | 117 项通过，无警告 |
| Node 桌面通信和日期回归 | 11 项通过 |
| `npm ci` 后类型检查与生产构建 | 通过；整合远端后重新构建通过 |
| 官方注册表 npm 完整审计 | 0 项已知漏洞 |
| `python -m pip check` | No broken requirements found |
| `cargo fmt --check` / `cargo check --locked` | 通过 |
| PowerShell 语法、版本及正确/错误 tag 检查 | 通过 |
| 审查阶段 PyInstaller Sidecar EXE 构建 | 通过，包含 tzdata；本地二进制不纳入 Git 提交 |
| 审查阶段冻结 EXE 隔离运行 | bootstrap、纽约时区设置、空日简报导出、正常 shutdown 全部通过 |
| `git diff --check` | 通过 |

PR 的首轮 GitHub Actions 在测试前因 Windows PowerShell 5.1 将无 BOM 中文脚本按 ANSI 解码而失败。已为版本校验脚本加入 UTF-8 BOM，显式以 UTF-8 读取版本文件，并将 CI 的脚本步骤改用 `pwsh`。本机 Windows PowerShell 5.1 下三个构建脚本语法检查、正确标签通过及错误标签拒绝均已验证，PowerShell 7 的版本校验也通过。该差异符合 [Microsoft 的脚本字符编码说明](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_character_encoding?view=powershell-7.5)。

浏览器在 1180/1280 像素桌面与 390 像素窄屏验证：

- 正文专有词“资料库”可命中对应文章。
- 251 篇模拟文章每页显示 50 篇，第二页起于第 51 篇，正文检索可命中第 226 篇。
- 慢旧查询在新查询完成后返回，不会覆盖新结果。
- 后台简报事件触发 reload，设置表单未保存的服务地址仍保留。
- 对 2026-10-23 简报点击重新生成，仍生成该日简报。
- 深色、浅色及跟随系统的切换生效；首页、文章、设置、简报没有水平溢出。
- 阅读抽屉获得焦点，Tab 留在抽屉内，Escape 关闭；窄屏导航目标至少 44px。
- 浏览器控制台没有 error 或 warning。

截图位于 `output/playwright/audit-desktop-briefing.png`、`audit-mobile-briefing.png`。输出目录被 Git 忽略。

## 界面审查结果与保留项

既有界面是内容优先的阅读工作台，没有本轮新增的渐变、玻璃效果、装饰卡片或与任务无关的动效。审查发现的主要界面问题是搜索范围、异步状态、主题、焦点和窄屏布局，均已在独立实现阶段处理。收尾修正了正文元信息对比度、深色分割线与窄屏触控尺寸。

保留既有 Windows 系统字体和桌面视觉层级；没有为“重构”引入另一套 UI 框架。现有部分 CSS 仍使用直接颜色值，后续若扩展视觉系统可以继续收敛为 token；本轮优先处理可见状态与功能正确性。

## 验证范围与后续边界

- 没有真实扫码、触发微信风控或调用付费 AI；外部接口兼容性仍需要可用测试会话验证。
- 没有生成签名安装包、安装或发布。签名路径分支做了静态检查；尝试假签名路径的完整脚本测试被自动审批以 `blocked by policy` 拒绝，已改做不涉及签名环境的隔离分支检查。
- 单源增量仍以 20 页为保护边界。超过边界明确报错并保留原游标；未来可增加分段追赶检查点，当前没有伪装成同步完成。
- 正在执行的 HTTP/浏览器请求需返回或超时后才能结束；关停会阻止后续请求和处理，未实现强制中断所有第三方调用。
- npm 公告检查不等于 Python、Rust 或整个应用的完整安全审计；`pip check` 仅校验依赖关系。
- 自动审批拒绝清理 `.pytest_audit_baseline/`、`.pytest_audit_final/` 及遗留二进制文件，未提供更具体理由。没有改用其他删除路径绕过审查，这些目录保留测试数据。

## 复现命令

从仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pip check
.\scripts\build_validate_versions.ps1 -Tag v3.0.0
.\scripts\build_sidecar.ps1

cd desktop
npm ci
npm test
npm run build
npm audit --registry=https://registry.npmjs.org

cd src-tauri
cargo fmt --check
cargo check --locked
```
