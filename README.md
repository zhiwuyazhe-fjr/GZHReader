# GZHReader

一个面向 Windows 的 RSS 日报工具：

- 不再依赖微信桌面端 UI 自动化
- 通过 `wewe-rss` 产出公众号 RSS / Atom 源
- Python 脚本直接拉取 RSS、过滤当天文章、去重、总结
- 输出按公众号分组的 Markdown 日报
- 可选注册 Windows 计划任务，每天固定时间执行

## 现在的推荐链路

`公众号 -> wewe-rss -> GZHReader -> Markdown 日报`

如果后续需要，再额外把 Markdown 结果推送到飞书、邮箱或知识库。

## 快速开始

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]

gzhreader init
gzhreader wewe-rss up
```

然后：

1. 打开 `http://localhost:4000`
2. 输入授权码，默认是 `123567`，也可以在 `config.yaml` / `infra/wewe-rss/.env` 里改
3. 在 `wewe-rss` 后台扫码登录
4. 添加你要订阅的公众号
5. 复制生成的 RSS / Atom 链接，填到 `config.yaml` 的 `feeds` 中
6. 运行：

```powershell
gzhreader doctor
gzhreader run today
gzhreader schedule install
```

## 常用命令

```powershell
gzhreader init
gzhreader doctor
gzhreader run today
gzhreader run date 2026-03-07
gzhreader run today --feed 新智元

gzhreader schedule install
gzhreader schedule remove

gzhreader wewe-rss init
gzhreader wewe-rss up
gzhreader wewe-rss down
gzhreader wewe-rss logs
```

## `config.yaml` 示例

```yaml
feeds:
  - name: 新智元
    url: http://localhost:4000/feeds/xinzhiyuan.atom
    active: true
    order: 1

rss:
  timezone: Asia/Shanghai
  day_start: "00:00"
  request_timeout_seconds: 20
  max_articles_per_feed: 20

wewe_rss:
  enabled: true
  base_url: http://localhost:4000
  auth_code: "123567"
  service_dir: ./infra/wewe-rss
  compose_variant: mysql

llm:
  base_url: https://api.openai.com/v1
  api_key: ""
  model: gpt-4o-mini

output:
  briefing_dir: ./output/briefings
  raw_archive_dir: ./output/raw
```

## `wewe-rss` 目录说明

初始化后会自动生成：

- `infra/wewe-rss/.env`
- `infra/wewe-rss/docker-compose.sqlite.yml`
- `infra/wewe-rss/docker-compose.mysql.yml`
- `infra/wewe-rss/docker-compose.yml`
- `infra/wewe-rss/data/`

默认使用 `sqlite` 版本，部署最简单；如果你后面想长期稳定运行，可以把 `compose_variant` 改成 `mysql`，再执行一次：

```powershell
gzhreader wewe-rss init --force
```

## 生成结果

日报默认输出到：

- `output/briefings/YYYY-MM-DD.md`

原始 RSS HTML 片段会归档到：

- `output/raw/`

## 迁移说明

旧版微信桌面端自动化代码已移除，配置会自动把旧的 `accounts` 迁移成新的 `feeds` 占位项，但你仍然需要补全每个 `feed.url`。
