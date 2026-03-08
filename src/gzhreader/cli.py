from __future__ import annotations

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
from .runtime_paths import DEFAULT_GUI_HOST, DEFAULT_GUI_PORT, ensure_runtime_dirs, resolve_config_path
from .scheduler import get_schedule_status, install_schedule, remove_schedule
from .service import ReaderService
from .storage import Storage
from .summarizer import OpenAICompatibleSummarizer
from .types import DoctorCheck
from .webapp import create_app as create_web_app
from .wewe_rss import WeWeRSSManager

app = typer.Typer(help="GZHReader GUI launcher and RSS workflow tools")
run_app = typer.Typer(help="Run daily briefing jobs")
schedule_app = typer.Typer(help="Manage Windows scheduled task")
wewe_app = typer.Typer(help="Manage bundled wewe-rss service")
app.add_typer(run_app, name="run")
app.add_typer(schedule_app, name="schedule")
app.add_typer(wewe_app, name="wewe-rss")


@app.command()
def init(
    config: Path | None = typer.Option(None, "--config", help="Config file path"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config"),
) -> None:
    config_path = _resolve_cli_config_path(config)
    if config_path.exists() and not force:
        raise typer.BadParameter(f"Config already exists: {config_path}")
    config_data = _bootstrap_config(config_path, force=force)

    manager = WeWeRSSManager(config_data.wewe_rss)
    generated = manager.ensure_scaffold(force=False)

    typer.echo(f"Created config: {config_path}")
    for path in generated:
        typer.echo(f"- scaffold: {path}")


@app.command("app")
def launch_app(
    config: Path | None = typer.Option(None, "--config", help="Config file path"),
    host: str = typer.Option(DEFAULT_GUI_HOST, "--host", help="GUI host"),
    port: int = typer.Option(DEFAULT_GUI_PORT, "--port", help="GUI port"),
    open_browser: bool = typer.Option(True, "--open-browser/--no-open-browser", help="Open browser automatically"),
) -> None:
    run_gui_server(config=config, host=host, port=port, open_browser=open_browser)


@app.command()
def doctor(config: Path | None = typer.Option(None, "--config", help="Config file path")) -> None:
    config_path = _resolve_cli_config_path(config)
    config_data = ensure_config(config_path)
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
    config: Path | None = typer.Option(None, "--config", help="Config file path"),
    feed: str | None = typer.Option(None, "--feed", help="Deprecated; aggregate source is always used"),
) -> None:
    _run_once(_resolve_cli_config_path(config), date.today(), feed)


@run_app.command("date")
def run_date(
    target_date: str,
    config: Path | None = typer.Option(None, "--config", help="Config file path"),
    feed: str | None = typer.Option(None, "--feed", help="Deprecated; aggregate source is always used"),
) -> None:
    parsed = datetime.strptime(target_date, "%Y-%m-%d").date()
    _run_once(_resolve_cli_config_path(config), parsed, feed)


@schedule_app.command("install")
def schedule_install_cmd(config: Path | None = typer.Option(None, "--config", help="Config file path")) -> None:
    config_path = _resolve_cli_config_path(config)
    config_data = ensure_config(config_path)
    configure_logging(config_data.output.log_level)
    typer.echo(install_schedule(config_data, config_path))


@schedule_app.command("remove")
def schedule_remove_cmd() -> None:
    typer.echo(remove_schedule())


@wewe_app.command("init")
def wewe_init(
    config: Path | None = typer.Option(None, "--config", help="Config file path"),
    force: bool = typer.Option(False, "--force", help="Overwrite docker compose scaffold"),
) -> None:
    config_data = ensure_config(_resolve_cli_config_path(config))
    manager = WeWeRSSManager(config_data.wewe_rss)
    for path in manager.ensure_scaffold(force=force):
        typer.echo(path)


@wewe_app.command("up")
def wewe_up(config: Path | None = typer.Option(None, "--config", help="Config file path")) -> None:
    config_data = ensure_config(_resolve_cli_config_path(config))
    manager = WeWeRSSManager(config_data.wewe_rss)
    typer.echo(manager.up())


@wewe_app.command("down")
def wewe_down(config: Path | None = typer.Option(None, "--config", help="Config file path")) -> None:
    config_data = ensure_config(_resolve_cli_config_path(config))
    manager = WeWeRSSManager(config_data.wewe_rss)
    typer.echo(manager.down())


@wewe_app.command("logs")
def wewe_logs(config: Path | None = typer.Option(None, "--config", help="Config file path")) -> None:
    config_data = ensure_config(_resolve_cli_config_path(config))
    manager = WeWeRSSManager(config_data.wewe_rss)
    typer.echo(manager.logs())


def run_gui_server(
    *,
    config: Path | None = None,
    host: str = DEFAULT_GUI_HOST,
    port: int = DEFAULT_GUI_PORT,
    open_browser: bool = True,
) -> None:
    config_path = _resolve_cli_config_path(config)
    config_data = _bootstrap_config(config_path, force=False)
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

    typer.echo(f"GZHReader GUI started: {base_url}")
    uvicorn.run(
        create_web_app(config_path=config_path),
        host=host,
        port=port,
        log_level=config_data.output.log_level.lower(),
        log_config=None,
    )


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


def _resolve_cli_config_path(config: Path | None) -> Path:
    return resolve_config_path(config)


def _bootstrap_config(config: Path, *, force: bool) -> AppConfig:
    config.parent.mkdir(parents=True, exist_ok=True)
    if config.exists() and not force:
        return ensure_config(config)
    if force and config.exists():
        config.unlink()

    ensure_runtime_dirs()
    config_data = default_config()
    save_config(config_data, config)

    Path(config_data.db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(config_data.output.briefing_dir).mkdir(parents=True, exist_ok=True)
    if config_data.output.save_raw_html:
        Path(config_data.output.raw_archive_dir).mkdir(parents=True, exist_ok=True)

    return config_data


def _run_once(config_path: Path, target_date: date, feed_filter: str | None) -> None:
    if feed_filter:
        typer.echo("`--feed` is deprecated and ignored because the app now uses `all.atom`.")

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
    checks: list[DoctorCheck] = [DoctorCheck(name="Config", ok=True, detail="config loaded")]

    try:
        import feedparser as _feedparser  # noqa: F401
    except Exception as exc:
        checks.append(DoctorCheck(name="RSS parser", ok=False, detail=str(exc)))
    else:
        checks.append(DoctorCheck(name="RSS parser", ok=True, detail="feedparser ready"))

    http_ok, http_detail = article_fetcher.check_http_runtime()
    checks.append(DoctorCheck(name="HTTP fulltext", ok=http_ok, detail=http_detail))

    browser_ok, browser_detail = article_fetcher.check_browser_runtime()
    checks.append(DoctorCheck(name="Browser fulltext", ok=browser_ok, detail=browser_detail))

    Path(config_data.db_path).parent.mkdir(parents=True, exist_ok=True)
    storage.init_db()
    checks.append(DoctorCheck(name="SQLite", ok=True, detail=f"database ready: {config_data.db_path}"))

    docker_ok, docker_detail = manager.check_docker()
    checks.append(DoctorCheck(name="Docker", ok=docker_ok, detail=docker_detail))

    generated = manager.ensure_scaffold(force=False)
    checks.append(DoctorCheck(name="wewe-rss scaffold", ok=True, detail=f"scaffold ready: {generated[-1]}"))

    if config_data.wewe_rss.enabled:
        service_ok, service_detail = manager.check_service()
        checks.append(DoctorCheck(name="wewe-rss service", ok=service_ok, detail=service_detail))

    runtime_feed = config_data.runtime_feed()
    if not runtime_feed.url:
        checks.append(DoctorCheck(name="Source", ok=False, detail="missing source.url"))
    else:
        ok, detail = rss_client.check_feed(runtime_feed)
        checks.append(DoctorCheck(name="Aggregate RSS", ok=ok, detail=detail))

    _, schedule_detail = get_schedule_status()
    checks.append(DoctorCheck(name="Schedule", ok=True, detail=schedule_detail))

    llm_ok, llm_detail = summarizer.check_connectivity()
    checks.append(DoctorCheck(name="LLM connectivity", ok=llm_ok, detail=llm_detail))
    return checks
