from __future__ import annotations

import socket
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
from .rss_service import BundledRSSServiceManager
from .runtime_paths import DEFAULT_GUI_HOST, DEFAULT_GUI_PORT, ensure_runtime_dirs, resolve_config_path
from .scheduler import get_schedule_status, install_schedule, remove_schedule
from .service import ReaderService
from .storage import Storage
from .summarizer import OpenAICompatibleSummarizer
from .types import DoctorCheck
from .webapp import DashboardBackend, create_app as create_web_app
from .weread_bridge import run_bridge_server

app = typer.Typer(help="GZHReader desktop workspace")
run_app = typer.Typer(help="Run briefing generation")
schedule_app = typer.Typer(help="Manage Windows scheduled task")
service_app = typer.Typer(help="Manage bundled RSS service")
app.add_typer(run_app, name="run")
app.add_typer(schedule_app, name="schedule")
app.add_typer(service_app, name="service")


@app.command()
def init(
    config: Path | None = typer.Option(None, "--config", help="Config file path"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing config"),
) -> None:
    config_path = _resolve_cli_config_path(config)
    if config_path.exists() and not force:
        raise typer.BadParameter(f"Config already exists: {config_path}")
    _bootstrap_config(config_path, force=force)
    typer.echo(f"Created config: {config_path}")


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

    checks = build_doctor_checks(config_data)
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
    feed: str | None = typer.Option(None, "--feed", help="Deprecated and ignored"),
) -> None:
    _run_once(_resolve_cli_config_path(config), date.today(), feed)


@run_app.command("date")
def run_date(
    target_date: str,
    config: Path | None = typer.Option(None, "--config", help="Config file path"),
    feed: str | None = typer.Option(None, "--feed", help="Deprecated and ignored"),
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


@service_app.command("start")
def service_start(config: Path | None = typer.Option(None, "--config", help="Config file path")) -> None:
    config_data = ensure_config(_resolve_cli_config_path(config))
    typer.echo(BundledRSSServiceManager(config_data.rss_service).start())


@service_app.command("stop")
def service_stop(config: Path | None = typer.Option(None, "--config", help="Config file path")) -> None:
    config_data = ensure_config(_resolve_cli_config_path(config))
    typer.echo(BundledRSSServiceManager(config_data.rss_service).stop())


@service_app.command("restart")
def service_restart(config: Path | None = typer.Option(None, "--config", help="Config file path")) -> None:
    config_data = ensure_config(_resolve_cli_config_path(config))
    typer.echo(BundledRSSServiceManager(config_data.rss_service).restart())


@service_app.command("status")
def service_status(config: Path | None = typer.Option(None, "--config", help="Config file path")) -> None:
    config_data = ensure_config(_resolve_cli_config_path(config))
    status = BundledRSSServiceManager(config_data.rss_service).status_snapshot()
    typer.echo(f"runtime: {'ok' if status.runtime_ok else 'fail'} - {status.runtime_detail}")
    typer.echo(f"process: {'ok' if status.process_ok else 'fail'} - {status.process_detail}")
    typer.echo(f"web: {'ok' if status.web_ok else 'fail'} - {status.web_detail}")
    typer.echo(f"admin: {status.admin_url}")
    typer.echo(f"feed: {status.feed_url}")


@service_app.command("logs")
def service_logs(config: Path | None = typer.Option(None, "--config", help="Config file path")) -> None:
    config_data = ensure_config(_resolve_cli_config_path(config))
    typer.echo(BundledRSSServiceManager(config_data.rss_service).logs())


@service_app.command("open-admin")
def service_open_admin(config: Path | None = typer.Option(None, "--config", help="Config file path")) -> None:
    config_data = ensure_config(_resolve_cli_config_path(config))
    typer.echo(BundledRSSServiceManager(config_data.rss_service).open_admin())


@app.command("bridge-serve", hidden=True)
def bridge_serve(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(18765, "--port"),
    remote_url: str = typer.Option("https://weread.111965.xyz", "--remote-url"),
    session_store: Path = typer.Option(..., "--session-store"),
) -> None:
    run_bridge_server(
        host=host,
        port=port,
        remote_platform_url=remote_url,
        session_store_path=session_store.expanduser().resolve(),
    )


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

    selected_port, launch_mode = _resolve_gui_port(host, port)
    base_url = f"http://{host}:{selected_port}"

    if launch_mode == "existing":
        typer.echo(f"GZHReader GUI is already running: {base_url}")
        if open_browser:
            webbrowser.open(base_url)
        return

    if launch_mode == "fallback":
        typer.echo(f"Port {port} is busy; GZHReader will use {selected_port} instead.")

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
        port=selected_port,
        log_level=config_data.output.log_level.lower(),
        log_config=None,
    )


def _can_bind_port(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _is_existing_gzhreader_gui(base_url: str) -> bool:
    try:
        response = httpx.get(f"{base_url}/healthz", timeout=1.0)
    except Exception:
        return False
    if response.status_code != 200:
        return False
    try:
        data = response.json()
    except Exception:
        return False
    return data == {"ok": True}


def _resolve_gui_port(host: str, preferred_port: int) -> tuple[int, str]:
    if _can_bind_port(host, preferred_port):
        return preferred_port, "new"

    preferred_base_url = f"http://{host}:{preferred_port}"
    if _is_existing_gzhreader_gui(preferred_base_url):
        return preferred_port, "existing"

    for candidate in range(preferred_port + 1, preferred_port + 20):
        if _can_bind_port(host, candidate):
            return candidate, "fallback"

    raise RuntimeError("No available GUI port was found between 8765 and 8784.")


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

    runtime_paths = ensure_runtime_dirs()
    config_data = default_config()
    save_config(config_data, config)

    Path(config_data.db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(config_data.rss_service.data_dir).mkdir(parents=True, exist_ok=True)
    Path(config_data.output.briefing_dir).mkdir(parents=True, exist_ok=True)
    Path(runtime_paths.logs_dir).mkdir(parents=True, exist_ok=True)
    if config_data.output.save_raw_html:
        Path(config_data.output.raw_archive_dir).mkdir(parents=True, exist_ok=True)

    return config_data


def _run_once(config_path: Path, target_date: date, feed_filter: str | None) -> None:
    if feed_filter:
        typer.echo("`--feed` is deprecated and ignored because the app now uses a single aggregate source.")

    config_data = ensure_config(config_path)
    configure_logging(config_data.output.log_level)
    ok, detail = DashboardBackend(config_path).run_now(target_date)
    if ok:
        typer.echo(detail)
        return
    typer.echo(detail)
    raise typer.Exit(code=1)


def build_doctor_checks(config_data: AppConfig) -> list[DoctorCheck]:
    rss_client = RSSClient(config_data.rss)
    storage = Storage(config_data.db_path)
    summarizer = OpenAICompatibleSummarizer(config_data.llm)
    article_fetcher = ArticleContentFetcher(config_data.article_fetch, config_data.rss)
    service_manager = BundledRSSServiceManager(config_data.rss_service)
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

    runtime_ok, runtime_detail = service_manager.check_runtime()
    checks.append(DoctorCheck(name="Bundled RSS runtime", ok=runtime_ok, detail=runtime_detail))

    process_ok, process_detail = service_manager.check_process()
    checks.append(DoctorCheck(name="Bundled RSS process", ok=process_ok, detail=process_detail))

    service_ok, service_detail = service_manager.check_service()
    checks.append(DoctorCheck(name="Bundled RSS web", ok=service_ok, detail=service_detail))

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
