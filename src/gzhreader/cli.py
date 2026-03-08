from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path

import typer

from .article_fetcher import ArticleContentFetcher
from .briefing import BriefingBuilder
from .config import AppConfig, default_config, ensure_config, load_config, save_config
from .logging_utils import configure_logging
from .rss_client import RSSClient
from .scheduler import install_schedule, remove_schedule
from .service import ReaderService
from .storage import Storage
from .summarizer import OpenAICompatibleSummarizer
from .types import DoctorCheck
from .wewe_rss import WeWeRSSManager

app = typer.Typer(help="RSS ??????????")
run_app = typer.Typer(help="?? RSS ???????")
schedule_app = typer.Typer(help="?? Windows ????")
wewe_app = typer.Typer(help="???? wewe-rss ??")
app.add_typer(run_app, name="run")
app.add_typer(schedule_app, name="schedule")
app.add_typer(wewe_app, name="wewe-rss")


@app.command()
def init(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="??????"),
    force: bool = typer.Option(False, "--force", help="????????"),
) -> None:
    if config.exists() and not force:
        raise typer.BadParameter(f"???????: {config}")
    if force and config.exists():
        config.unlink()

    cfg = default_config()
    example = Path("config.example.yaml")
    if example.exists():
        shutil.copyfile(example, config)
        cfg = load_config(config)
    else:
        save_config(cfg, config)

    Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    Path(cfg.output.briefing_dir).mkdir(parents=True, exist_ok=True)
    if cfg.output.save_raw_html:
        Path(cfg.output.raw_archive_dir).mkdir(parents=True, exist_ok=True)
    manager = WeWeRSSManager(cfg.wewe_rss)
    generated = manager.ensure_scaffold(force=False)
    typer.echo(f"??????: {config}")
    for path in generated:
        typer.echo(f"- ???: {path}")


@app.command()
def doctor(config: Path = typer.Option(Path("config.yaml"), "--config", help="??????")) -> None:
    cfg = ensure_config(config)
    configure_logging(cfg.output.log_level)
    rss_client = RSSClient(cfg.rss)
    storage = Storage(cfg.db_path)
    summarizer = OpenAICompatibleSummarizer(cfg.llm)
    manager = WeWeRSSManager(cfg.wewe_rss)
    article_fetcher = ArticleContentFetcher(cfg.article_fetch, cfg.rss)
    checks = build_doctor_checks(cfg, rss_client, storage, summarizer, manager, article_fetcher)
    failed = False
    for check in checks:
        prefix = "[OK]" if check.ok else "[FAIL]"
        typer.echo(f"{prefix} {check.name}: {check.detail}")
        failed = failed or not check.ok
    if failed:
        raise typer.Exit(code=1)


@run_app.command("today")
def run_today(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="??????"),
    feed: str | None = typer.Option(None, "--feed", help="????? feed ??"),
) -> None:
    _run_once(config, date.today(), feed)


@run_app.command("date")
def run_date(
    target_date: str,
    config: Path = typer.Option(Path("config.yaml"), "--config", help="??????"),
    feed: str | None = typer.Option(None, "--feed", help="????? feed ??"),
) -> None:
    parsed = datetime.strptime(target_date, "%Y-%m-%d").date()
    _run_once(config, parsed, feed)


@schedule_app.command("install")
def schedule_install(config: Path = typer.Option(Path("config.yaml"), "--config", help="??????")) -> None:
    cfg = ensure_config(config)
    configure_logging(cfg.output.log_level)
    typer.echo(install_schedule(cfg, config))


@schedule_app.command("remove")
def schedule_remove() -> None:
    typer.echo(remove_schedule())


@wewe_app.command("init")
def wewe_init(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="??????"),
    force: bool = typer.Option(False, "--force", help="???? docker compose ??"),
) -> None:
    cfg = ensure_config(config)
    manager = WeWeRSSManager(cfg.wewe_rss)
    for path in manager.ensure_scaffold(force=force):
        typer.echo(path)


@wewe_app.command("up")
def wewe_up(config: Path = typer.Option(Path("config.yaml"), "--config", help="??????")) -> None:
    cfg = ensure_config(config)
    manager = WeWeRSSManager(cfg.wewe_rss)
    typer.echo(manager.up())


@wewe_app.command("down")
def wewe_down(config: Path = typer.Option(Path("config.yaml"), "--config", help="??????")) -> None:
    cfg = ensure_config(config)
    manager = WeWeRSSManager(cfg.wewe_rss)
    typer.echo(manager.down())


@wewe_app.command("logs")
def wewe_logs(config: Path = typer.Option(Path("config.yaml"), "--config", help="??????")) -> None:
    cfg = ensure_config(config)
    manager = WeWeRSSManager(cfg.wewe_rss)
    typer.echo(manager.logs())


def _run_once(config_path: Path, target_date: date, feed_filter: str | None) -> None:
    cfg = ensure_config(config_path)
    configure_logging(cfg.output.log_level)
    storage = Storage(cfg.db_path)
    service = ReaderService(
        config=cfg,
        storage=storage,
        rss_client=RSSClient(cfg.rss),
        summarizer=OpenAICompatibleSummarizer(cfg.llm),
        briefing_builder=BriefingBuilder(),
        article_fetcher=ArticleContentFetcher(cfg.article_fetch, cfg.rss),
    )
    result = service.run_for_date(target_date, feed_filter=feed_filter)
    typer.echo(
        f"run={result.run_key} collected={result.collected} inserted={result.inserted} "
        f"summarized={result.summarized} filtered={result.filtered_out}"
    )
    typer.echo(f"briefing={result.briefing_path}")
    for name, error in result.feed_errors.items():
        typer.echo(f"error {name}: {error}")


def build_doctor_checks(
    cfg: AppConfig,
    rss_client: RSSClient,
    storage: Storage,
    summarizer: OpenAICompatibleSummarizer,
    manager: WeWeRSSManager,
    article_fetcher: ArticleContentFetcher,
) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = [DoctorCheck(name="????", ok=True, detail="?????")]

    try:
        import feedparser as _feedparser  # noqa: F401

        checks.append(DoctorCheck(name="RSS ??", ok=True, detail="feedparser ??"))
    except Exception as exc:
        checks.append(DoctorCheck(name="RSS ??", ok=False, detail=str(exc)))

    http_ok, http_detail = article_fetcher.check_http_runtime()
    checks.append(DoctorCheck(name="HTTP ????", ok=http_ok, detail=http_detail))

    browser_ok, browser_detail = article_fetcher.check_browser_runtime()
    checks.append(DoctorCheck(name="?????????", ok=browser_ok, detail=browser_detail))

    Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    storage.init_db()
    checks.append(DoctorCheck(name="SQLite", ok=True, detail=f"???????: {cfg.db_path}"))

    docker_ok, docker_detail = manager.check_docker()
    checks.append(DoctorCheck(name="Docker", ok=docker_ok, detail=docker_detail))

    generated = manager.ensure_scaffold(force=False)
    checks.append(DoctorCheck(name="wewe-rss ???", ok=True, detail=f"???: {generated[-1]}"))

    if cfg.wewe_rss.enabled:
        service_ok, service_detail = manager.check_service()
        checks.append(DoctorCheck(name="wewe-rss ??", ok=service_ok, detail=service_detail))

    active_feeds = [feed for feed in cfg.feeds if feed.active]
    if not active_feeds:
        checks.append(DoctorCheck(name="RSS ?", ok=False, detail="????? feeds"))
    else:
        missing = [feed.name for feed in active_feeds if not feed.url]
        if missing:
            checks.append(DoctorCheck(name="RSS ?", ok=False, detail=f"?? feed ?? url: {', '.join(missing)}"))
        else:
            for feed in active_feeds[:5]:
                ok, detail = rss_client.check_feed(feed)
                checks.append(DoctorCheck(name=f"RSS ? {feed.name}", ok=ok, detail=detail))

    llm_ok, llm_detail = summarizer.check_connectivity()
    checks.append(DoctorCheck(name="LLM ??", ok=llm_ok, detail=llm_detail))
    return checks
