# GZHReader Desktop

桌面端使用 Tauri 2、Vue 3、TypeScript 和 Vite。开发及测试需要 Node.js 22.19+。

```powershell
npm ci
npm run dev
npm test
npm run build
npm run desktop:dev
npm run desktop:build
```

`desktop:dev` 和 `desktop:build` 需要：

- `src-tauri/binaries/gzhreader-core-x86_64-pc-windows-msvc.exe`
- Rust stable（最低 1.89）
- Visual Studio Build Tools 2022（MSVC C++ 与 Windows SDK）

Sidecar 请从仓库根目录运行 `scripts/build_sidecar.ps1` 生成。

业务状态和事件订阅集中在 `src/composables/useWorkspace.ts`，阅读抽屉独立为 `src/components/ArticleDrawer.vue`。`src/lib/rpc-client.ts` 负责桌面进程与请求生命周期，`src/lib/preview.ts` 仅提供浏览器预览数据。浏览器预览不会连接真实微信或摘要服务。

全部文章通过本地核心查询，每页 50 篇；搜索覆盖数据库中的标题、正文、摘要和公众号名称，不再限于首页加载的最近文章。
