from __future__ import annotations

import shutil
import threading
import time
import webbrowser
from datetime import date, datetime
from pathlib import Path

import httpx
import typer
import uvicorn

from .article_fetcher import ArticleContentFetcher
from .briefing import BriefingBuilder
from .config import AppConfig, default_config, ensure_config, save_config
from .logging_utils import configure_logging
from .rss_client import RSSClient
from .scheduler import get_schedule_status, install_schedule, remove_schedule
from .service import ReaderService
from .storage import Storage
from .summarizer import OpenAICompatibleSummarizer
from .types import DoctorCheck
from .webapp import create_app as create_web_app
from .wewe_rss import WeWeRSSManager

app = typer.Typer(help="GZHReader：GUI 优先的微信公众号 RSS 日报工具")
run_app = typer.Typer(help="高级入口：执行日报任务")
schedule_app = typer.Typer(help="高级入口：管理 Windows 计划任务")
wewe_app = typer.Typer(help="高级入口：管理 wewe-rss 服务")
app.add_typer(run_app, name="run")
app.add_typer(schedule_app, name="schedule")
app.add_typer(wewe_app, name="wewe-rss")


@app.command()
def init(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径"),
    force: bool = typer.Option(False, "--force", help="覆盖已有配置文件"),
) -> None:
    if config.exists() and not force:
        raise typer.BadParameter(f"配置文件已存在：{config}")
    config_data = _bootstrap_config(config, force=force)

    manager = WeWeRSSManager(config_data.wewe_rss)
    generated = manager.ensure_scaffold(force=False)

    typer.echo(f"已初始化配置：{config}")
    for path in generated:
        typer.echo(f"- 已生成：{path}")


@app.command("app")
def launch_app(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径"),
    host: str = typer.Option("127.0.0.1", "--host", help="GUI 监听地址"),
    port: int = typer.Option(8765, "--port", help="GUI 监听端口"),
    open_browser: bool = typer.Option(True, "--open-browser/--no-open-browser", help="启动后自动打开浏览器"),
) -> None:
    config_data = _bootstrap_config(config, force=False)
    configure_logging(config_data.output.log_level)
    WeWeRSSManager(config_data.wewe_rss).ensure_scaffold(force=False)

    base_url = f"http://{host}:{port}"
    if open_browser:
        threading.Thread(
            target=_wait_for_health_and_open_browser,
            args=(base_url,),
            kwargs={"timeout_seconds": 20.0, "interval_seconds": 0.25},
            daemon=True,
        ).start()

    typer.echo(f"GZHReader GUI 已启动：{base_url}")
    uvicorn.run(create_web_app(config_path=config), host=host, port=port, log_level=config_data.output.log_level.lower())


def _wait_for_health_and_open_browser(base_url: str, *, timeout_seconds: float, interval_seconds: float) -> bool:
    health_url = f"{base_url}/healthz"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            response = httpx.get(health_url, timeout=1.0)
            if response.status_code == 200:
                webbrowser.open(base_url)
                return True
        except Exception:
            pass
        time.sleep(interval_seconds)
    return False


def _bootstrap_config(config: Path, *, force: bool) -> AppConfig:
    if config.exists() and not force:
        return ensure_config(config)
    if force and config.exists():
        config.unlink()

    config_data = default_config()
    example = Path("config.example.yaml")
    if example.exists():
        shutil.copyfile(example, config)
        config_data = ensure_config(config)
    else:
        save_config(config_data, config)

    Path(config_data.db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(config_data.output.briefing_dir).mkdir(parents=True, exist_ok=True)
    if config_data.output.save_raw_html:
        Path(config_data.output.raw_archive_dir).mkdir(parents=True, exist_ok=True)

    return config_data


@app.command()
def doctor(config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径")) -> None:
    config_data = ensure_config(config)
    configure_logging(config_data.output.log_level)

    rss_client = RSSClient(config_data.rss)
    storage = Storage(config_data.db_path)
    summarizer = OpenAICompatibleSummarizer(config_data.llm)
    manager = WeWeRSSManager(config_data.wewe_rss)
    article_fetcher = ArticleContentFetcher(config_data.article_fetch, config_data.rss)
    checks = build_doctor_checks(config_data, rss_client, storage, summarizer, manager, article_fetcher)

    failed = False
    for check in checks:
        prefix = "[OK]" if check.ok else "[FAIL]"
        typer.echo(f"{prefix} {check.name}: {check.detail}")
        failed = failed or not check.ok
    if failed:
        raise typer.Exit(code=1)


@run_app.command("today")
def run_today(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径"),
    feed: str | None = typer.Option(None, "--feed", help="已废弃。当前版本固定读取聚合源。"),
) -> None:
    _run_once(config, date.today(), feed)


@run_app.command("date")
def run_date(
    target_date: str,
    config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径"),
    feed: str | None = typer.Option(None, "--feed", help="已废弃。当前版本固定读取聚合源。"),
) -> None:
    parsed = datetime.strptime(target_date, "%Y-%m-%d").date()
    _run_once(config, parsed, feed)


@schedule_app.command("install")
def schedule_install_cmd(config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径")) -> None:
    config_data = ensure_config(config)
    configure_logging(config_data.output.log_level)
    typer.echo(install_schedule(config_data, config))


@schedule_app.command("remove")
def schedule_remove_cmd() -> None:
    typer.echo(remove_schedule())


@wewe_app.command("init")
def wewe_init(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径"),
    force: bool = typer.Option(False, "--force", help="重写 docker compose 文件"),
) -> None:
    config_data = ensure_config(config)
    manager = WeWeRSSManager(config_data.wewe_rss)
    for path in manager.ensure_scaffold(force=force):
        typer.echo(path)


@wewe_app.command("up")
def wewe_up(config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径")) -> None:
    config_data = ensure_config(config)
    manager = WeWeRSSManager(config_data.wewe_rss)
    typer.echo(manager.up())


@wewe_app.command("down")
def wewe_down(config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径")) -> None:
    config_data = ensure_config(config)
    manager = WeWeRSSManager(config_data.wewe_rss)
    typer.echo(manager.down())


@wewe_app.command("logs")
def wewe_logs(config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径")) -> None:
    config_data = ensure_config(config)
    manager = WeWeRSSManager(config_data.wewe_rss)
    typer.echo(manager.logs())


def _run_once(config_path: Path, target_date: date, feed_filter: str | None) -> None:
    if feed_filter:
        typer.echo("提示：`--feed` 已废弃，当前版本固定读取聚合源 `all.atom`。")

    config_data = ensure_config(config_path)
    configure_logging(config_data.output.log_level)
    storage = Storage(config_data.db_path)
    service = ReaderService(
        config=config_data,
        storage=storage,
        rss_client=RSSClient(config_data.rss),
        summarizer=OpenAICompatibleSummarizer(config_data.llm),
        briefing_builder=BriefingBuilder(),
        article_fetcher=ArticleContentFetcher(config_data.article_fetch, config_data.rss),
    )
    result = service.run_for_date(target_date, feed_filter=None)
    typer.echo(
        f"run={result.run_key} collected={result.collected} inserted={result.inserted} "
        f"summarized={result.summarized} filtered={result.filtered_out}"
    )
    typer.echo(f"briefing={result.briefing_path}")
    for name, error in result.feed_errors.items():
        typer.echo(f"error {name}: {error}")


def build_doctor_checks(
    config_data: AppConfig,
    rss_client: RSSClient,
    storage: Storage,
    summarizer: OpenAICompatibleSummarizer,
    manager: WeWeRSSManager,
    article_fetcher: ArticleContentFetcher,
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = [DoctorCheck(name="配置文件", ok=True, detail="配置可读取")]

    try:
        import feedparser as _feedparser  # noqa: F401
    except Exception as exc:
        checks.append(DoctorCheck(name="RSS 解析", ok=False, detail=str(exc)))
    else:
        checks.append(DoctorCheck(name="RSS 解析", ok=True, detail="feedparser 可用"))

    http_ok, http_detail = article_fetcher.check_http_runtime()
    checks.append(DoctorCheck(name="HTTP 正文抓取", ok=http_ok, detail=http_detail))

    browser_ok, browser_detail = article_fetcher.check_browser_runtime()
    checks.append(DoctorCheck(name="浏览器正文抓取", ok=browser_ok, detail=browser_detail))

    Path(config_data.db_path).parent.mkdir(parents=True, exist_ok=True)
    storage.init_db()
    checks.append(DoctorCheck(name="SQLite", ok=True, detail=f"数据库可用：{config_data.db_path}"))

    docker_ok, docker_detail = manager.check_docker()
    checks.append(DoctorCheck(name="Docker", ok=docker_ok, detail=docker_detail))

    generated = manager.ensure_scaffold(force=False)
    checks.append(DoctorCheck(name="wewe-rss 编排", ok=True, detail=f"已准备：{generated[-1]}"))

    if config_data.wewe_rss.enabled:
        service_ok, service_detail = manager.check_service()
        checks.append(DoctorCheck(name="wewe-rss 服务", ok=service_ok, detail=service_detail))

    runtime_feed = config_data.runtime_feed()
    if not runtime_feed.url:
        checks.append(DoctorCheck(name="聚合源", ok=False, detail="缺少 source.url"))
    else:
        ok, detail = rss_client.check_feed(runtime_feed)
        checks.append(DoctorCheck(name="聚合源 RSS", ok=ok, detail=detail))

    schedule = get_schedule_status()
    checks.append(DoctorCheck(name="计划任务", ok=True, detail=schedule[1]))

    llm_ok, llm_detail = summarizer.check_connectivity()
    checks.append(DoctorCheck(name="LLM 接口", ok=llm_ok, detail=llm_detail))
    return checks
