from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path

import typer

from .briefing import BriefingBuilder
from .config import default_config, ensure_config, load_config, save_config
from .logging_utils import configure_logging
from .rss_client import RSSClient
from .scheduler import install_schedule, remove_schedule
from .service import ReaderService
from .storage import Storage
from .summarizer import OpenAICompatibleSummarizer
from .types import DoctorCheck
from .wewe_rss import WeWeRSSManager

app = typer.Typer(help="RSS 版微信公众号日报工具")
run_app = typer.Typer(help="执行一次 RSS 拉取与日报生成")
schedule_app = typer.Typer(help="管理 Windows 计划任务")
wewe_app = typer.Typer(help="管理本地 wewe-rss 服务")
app.add_typer(run_app, name="run")
app.add_typer(schedule_app, name="schedule")
app.add_typer(wewe_app, name="wewe-rss")


@app.command()
def init(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径"),
    force: bool = typer.Option(False, "--force", help="覆盖已有配置文件"),
) -> None:
    if config.exists() and not force:
        raise typer.BadParameter(f"配置文件已存在: {config}")
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
    Path(cfg.output.raw_archive_dir).mkdir(parents=True, exist_ok=True)
    manager = WeWeRSSManager(cfg.wewe_rss)
    generated = manager.ensure_scaffold(force=False)
    typer.echo(f"已初始化配置: {config}")
    for path in generated:
        typer.echo(f"- 已生成: {path}")


@app.command()
def doctor(config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径")) -> None:
    cfg = ensure_config(config)
    configure_logging(cfg.output.log_level)
    rss_client = RSSClient(cfg.rss)
    storage = Storage(cfg.db_path)
    summarizer = OpenAICompatibleSummarizer(cfg.llm)
    manager = WeWeRSSManager(cfg.wewe_rss)
    checks = build_doctor_checks(cfg, rss_client, storage, summarizer, manager)
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
    feed: str | None = typer.Option(None, "--feed", help="只跑某一个 feed 名称"),
) -> None:
    _run_once(config, date.today(), feed)


@run_app.command("date")
def run_date(
    target_date: str,
    config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径"),
    feed: str | None = typer.Option(None, "--feed", help="只跑某一个 feed 名称"),
) -> None:
    parsed = datetime.strptime(target_date, "%Y-%m-%d").date()
    _run_once(config, parsed, feed)


@schedule_app.command("install")
def schedule_install(config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径")) -> None:
    cfg = ensure_config(config)
    configure_logging(cfg.output.log_level)
    typer.echo(install_schedule(cfg, config))


@schedule_app.command("remove")
def schedule_remove() -> None:
    typer.echo(remove_schedule())


@wewe_app.command("init")
def wewe_init(
    config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径"),
    force: bool = typer.Option(False, "--force", help="覆盖已存在的 docker compose 入口文件"),
) -> None:
    cfg = ensure_config(config)
    manager = WeWeRSSManager(cfg.wewe_rss)
    for path in manager.ensure_scaffold(force=force):
        typer.echo(path)


@wewe_app.command("up")
def wewe_up(config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径")) -> None:
    cfg = ensure_config(config)
    manager = WeWeRSSManager(cfg.wewe_rss)
    typer.echo(manager.up())


@wewe_app.command("down")
def wewe_down(config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径")) -> None:
    cfg = ensure_config(config)
    manager = WeWeRSSManager(cfg.wewe_rss)
    typer.echo(manager.down())


@wewe_app.command("logs")
def wewe_logs(config: Path = typer.Option(Path("config.yaml"), "--config", help="配置文件路径")) -> None:
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
    )
    result = service.run_for_date(target_date, feed_filter=feed_filter)
    typer.echo(
        f"run={result.run_key} collected={result.collected} inserted={result.inserted} "
        f"summarized={result.summarized} filtered={result.filtered_out}"
    )
    typer.echo(f"briefing={result.briefing_path}")
    for name, error in result.feed_errors.items():
        typer.echo(f"error {name}: {error}")


def build_doctor_checks(cfg, rss_client, storage, summarizer, manager) -> list[DoctorCheck]:
    checks: list[DoctorCheck] = []
    checks.append(DoctorCheck(name="配置文件", ok=True, detail="配置可读取"))

    try:
        import feedparser as _  # noqa: F401
        checks.append(DoctorCheck(name="RSS 依赖", ok=True, detail="feedparser 可用"))
    except Exception as exc:
        checks.append(DoctorCheck(name="RSS 依赖", ok=False, detail=str(exc)))

    Path(cfg.db_path).parent.mkdir(parents=True, exist_ok=True)
    storage.init_db()
    checks.append(DoctorCheck(name="SQLite", ok=True, detail=f"数据库可初始化: {cfg.db_path}"))

    docker_ok, docker_detail = manager.check_docker()
    checks.append(DoctorCheck(name="Docker", ok=docker_ok, detail=docker_detail))

    generated = manager.ensure_scaffold(force=False)
    checks.append(DoctorCheck(name="wewe-rss 脚手架", ok=True, detail=f"已就绪: {generated[-1]}"))

    if cfg.wewe_rss.enabled:
        service_ok, service_detail = manager.check_service()
        checks.append(DoctorCheck(name="wewe-rss 服务", ok=service_ok, detail=service_detail))

    active_feeds = [feed for feed in cfg.feeds if feed.active]
    if not active_feeds:
        checks.append(DoctorCheck(name="RSS 源", ok=False, detail="没有启用的 feeds"))
    else:
        missing = [feed.name for feed in active_feeds if not feed.url]
        if missing:
            checks.append(DoctorCheck(name="RSS 源", ok=False, detail=f"这些 feed 缺少 url: {', '.join(missing)}"))
        else:
            for feed in active_feeds[:5]:
                ok, detail = rss_client.check_feed(feed)
                checks.append(DoctorCheck(name=f"RSS 源 {feed.name}", ok=ok, detail=detail))

    llm_ok, llm_detail = summarizer.check_connectivity()
    checks.append(DoctorCheck(name="LLM 接口", ok=llm_ok, detail=llm_detail))
    return checks
