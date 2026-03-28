# bundled `wewe-rss`

这里保留的是已经接管到 `GZHReader` 主仓库里的 `wewe-rss` 源码。

## 当前定位

- 这是 `GZHReader` 的内置公众号后台源码
- 当前维护目标是 `Windows + SQLite-only`
- 不再保留 Docker、MySQL、compose 作为主路径
- 用户最终只会通过 `GZHReader` 进入这个后台

## 本地开发

在仓库根目录使用统一脚本构建 runtime：

```powershell
.\scripts\build_wewe_rss.ps1
```

如果只想单独调试前后端，可以在 `third_party/wewe-rss` 下运行：

```powershell
corepack pnpm@8.15.8 install --frozen-lockfile
corepack pnpm@8.15.8 --filter web build
corepack pnpm@8.15.8 --filter server build
```

运行时默认使用 SQLite：

- `DATABASE_TYPE=sqlite`
- `DATABASE_URL=file:../data/wewe-rss.db`
- bundled 模式下默认直接进入后台，不再额外要求访问码

## 说明

- 这里的 UI、品牌、主题和版本展示已经按 `GZHReader v2.0.0` 接管
- 上游许可证保留在 `LICENSE`
- 第三方来源和集成说明见仓库根目录 `THIRD_PARTY_NOTICES.md`
