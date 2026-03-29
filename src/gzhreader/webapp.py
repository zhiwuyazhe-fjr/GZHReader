from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from html import escape
import logging
from pathlib import Path
import threading
import time
from typing import Callable
from urllib.parse import urlencode
from uuid import uuid4

from markdown import markdown
import yaml
from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__
from .article_fetcher import ArticleContentFetcher
from .briefing import BriefingBuilder
from .config import (
    DAILY_ARTICLE_LIMIT_PRESETS,
    AppConfig,
    describe_daily_article_limit,
    ensure_config,
    normalize_daily_article_limit,
    save_config,
)
from .embedded_assets import HTMX_MIN_JS
from .logging_utils import configure_logging
from .platform_utils import open_local_path
from .rss_client import RSSClient
from .rss_service import BundledRSSServiceManager
from .runtime_paths import get_resource_root, get_runtime_paths, resolve_config_path
from .scheduler import get_schedule_status, install_schedule, remove_schedule
from .service import ReaderService
from .storage import Storage
from .summarizer import OpenAICompatibleSummarizer, resolve_api_key

LOGGER = logging.getLogger(__name__)
LLM_API_KEY_PLACEHOLDER = "__GZHREADER_KEEP_EXISTING_API_KEY__"
BRIEFING_FILENAME_RE = r"^\d{4}-\d{2}-\d{2}(?:_\d+)?\.md$"


def _build_theme_state() -> dict[str, object]:
    return {
        "default": "system",
        "options": [
            {"value": "system", "label": "跟随系统"},
            {"value": "light", "label": "浅色"},
            {"value": "dark", "label": "深色"},
        ],
    }


def _build_about_modal() -> dict[str, object]:
    return {
        "button_label": "关于",
        "dialog_id": "about-dialog",
        "tagline": "把公众号阅读整理成更安静的本地工作台",
        "motivation_title": "💡 开发动机",
        "motivation_text": "从公众号碎片化的推送中解放出来，比起被小红点牵着走，更希望把优质内容安静地收进本地、沉淀为日报，留给真正需要深度阅读的时刻😍",
        "repo_url": "https://github.com/zhiwuyazhe-fjr/GZHReader",
        "feedback_title": "💬 问题反馈",
        "feedback_text": "遇到账号配置、内容抓取、日报生成或交互体验上的问题，欢迎随时提 Issue！特别是那些让你觉得“多点了一步”、“等得太久”或“提示看不懂”的细节，都是接下来的优化方向！",
        "issues_url": "https://github.com/zhiwuyazhe-fjr/GZHReader/issues",
        "feedback_label": "反馈问题",
        "support_title": "❤️ 支持项目",
        "support_text": "如果 GZHReader 帮你减少了信息噪音，让阅读整理更省心，请把它分享给有同样困扰的朋友。每一次安利，都是我持续优化的动力😘",
        "share_url": "https://github.com/zhiwuyazhe-fjr/GZHReader",
        "support_label": "分享给朋友",
        "author_title": "关于作者",
        "author_name": "zhiwuyazhe_fjr",
        "author_lines": [
            "📍 TJU | CS 在读",
            "🚀 AI 探索者 | 预备役创业者",
            "✨ Elon Musk 信徒",
        ],
        "author_github_url": "https://github.com/zhiwuyazhe-fjr",
        "author_github_label": "GitHub主页",
        "author_xhs_label": "小红书",
        "author_xhs_image_url": "/static/brand/xhs.jpg",
        "footer_lines": [
            "本地优先 · 公众号阅读工作台 · Markdown 日报",
            "把每天的推送整理成真正值得回看的内容",
        ],
    }


def _display_version(value: str) -> str:
    normalized = value.strip()
    while normalized[:1] in {"v", "V"}:
        normalized = normalized[1:]
    return normalized or value


def _resolve_resource_dir(kind: str) -> Path:
    resource_root = get_resource_root()
    candidates = [
        resource_root / "gzhreader" / kind,
        resource_root / "src" / "gzhreader" / kind,
        Path(__file__).with_name(kind),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _create_templates() -> Jinja2Templates:
    return Jinja2Templates(directory=str(_resolve_resource_dir("templates")))


def _get_static_dir() -> Path:
    return _resolve_resource_dir("static")


@dataclass(slots=True)
class BriefingFile:
    name: str
    date_text: str
    path: str


@dataclass(slots=True)
class RunJobStatus:
    id: str
    target_date: str
    status: str = "running"
    stage: str = "正在准备"
    detail: str = ""
    level: str = "info"
    started_at: float = 0.0
    finished_at: float | None = None


class RunJobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, RunJobStatus] = {}
        self._lock = threading.Lock()

    def create(self, target_date: date) -> RunJobStatus:
        job = RunJobStatus(
            id=uuid4().hex,
            target_date=target_date.isoformat(),
            stage="正在检查账号",
            detail="准备刷新订阅并生成日报",
            started_at=time.time(),
        )
        with self._lock:
            self._jobs[job.id] = job
        return job

    def update(self, job_id: str, **changes: str | float | None) -> RunJobStatus | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            for key, value in changes.items():
                setattr(job, key, value)
            return job

    def get(self, job_id: str) -> RunJobStatus | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return RunJobStatus(
                id=job.id,
                target_date=job.target_date,
                status=job.status,
                stage=job.stage,
                detail=job.detail,
                level=job.level,
                started_at=job.started_at,
                finished_at=job.finished_at,
            )


def _build_llm_status(config: AppConfig) -> dict[str, object]:
    api_key, api_key_source = resolve_api_key(config.llm)
    configured = bool(config.llm.base_url.strip() and config.llm.model.strip() and api_key)

    if configured:
        if api_key_source == "config":
            detail = "AI 模型已经就绪，保存的密钥会继续用于生成摘要"
        else:
            detail = "AI 模型已经就绪，当前正在使用环境变量里的密钥"
    else:
        detail = "还没有完成 AI 模型配置"

    return {
        "configured": configured,
        "detail": detail,
        "api_key_source": api_key_source,
        "api_key_saved": bool(config.llm.api_key.strip()),
        "uses_env_api_key": api_key_source == "env",
    }


def _build_redacted_yaml(config: AppConfig) -> str:
    payload = config.model_dump(mode="json", exclude={"feeds"})
    llm_payload = payload.get("llm")
    if isinstance(llm_payload, dict) and llm_payload.get("api_key"):
        llm_payload["api_key"] = LLM_API_KEY_PLACEHOLDER
    rss_service_payload = payload.get("rss_service")
    if isinstance(rss_service_payload, dict):
        rss_service_payload.pop("auth_code", None)
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


def _render_markdown(content: str) -> str:
    normalized_content = content.lstrip("\ufeff")
    return markdown(
        normalized_content,
        extensions=["extra", "fenced_code", "sane_lists", "tables", "toc"],
        output_format="html5",
    )


def _display_path(value: str) -> str:
    try:
        return str(Path(value).expanduser().resolve())
    except Exception:
        return value


def _build_daily_time(run_hour: int, run_minute: int) -> str:
    if not (0 <= run_hour <= 23):
        raise ValueError("小时必须在 0 到 23 之间")
    if not (0 <= run_minute <= 59):
        raise ValueError("分钟必须在 0 到 59 之间")
    return f"{run_hour:02d}:{run_minute:02d}"


def _split_daily_time(value: str) -> tuple[int, int]:
    hour, minute = value.split(":", 1)
    return int(hour), int(minute)


def _build_daily_limit_options(current_limit: str | int) -> list[dict[str, str]]:
    normalized = normalize_daily_article_limit(current_limit)
    values: list[str | int] = list(DAILY_ARTICLE_LIMIT_PRESETS)
    if normalized not in values:
        values = [normalized, *values]

    options: list[dict[str, str]] = []
    for value in values:
        if value == "all":
            label = "当天全部"
        elif value == normalized and value not in DAILY_ARTICLE_LIMIT_PRESETS:
            label = f"当前高级值：每天最多 {value} 篇"
        else:
            label = f"每天最多 {value} 篇"
        options.append({"value": str(value), "label": label})
    return options


def _normalize_output_dir(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("保存目录不能为空")
    return _display_path(cleaned)


def _choose_output_dir(initial_dir: str) -> str | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:
        raise RuntimeError("当前环境无法打开系统目录选择器，请手动输入路径") from exc

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    try:
        selected = filedialog.askdirectory(
            initialdir=_display_path(initial_dir),
            title="选择 Markdown 日报保存目录",
            mustexist=False,
        )
    finally:
        root.destroy()
    return selected or None


class DashboardBackend:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    def load_config(self) -> AppConfig:
        return ensure_config(self.config_path)

    def save_config(self, config: AppConfig) -> None:
        save_config(config, self.config_path)

    def build_status_placeholder(self, config: AppConfig) -> dict[str, object]:
        llm_status = _build_llm_status(config)
        return {
            "service": {
                "runtime_ok": None,
                "runtime_detail": "正在检查运行时",
                "process_ok": None,
                "process_detail": "正在检查本地服务",
                "web_ok": None,
                "web_detail": "正在确认后台是否可访问",
                "admin_url": f"{config.rss_service.base_url.rstrip('/')}/dash",
                "feed_url": f"{config.rss_service.base_url.rstrip('/')}/feeds/all.atom",
            },
            "article_fetch": {
                "http_ok": None,
                "http_detail": "正在检查",
                "browser_ok": None,
                "browser_detail": "正在检查",
            },
            "llm": llm_status,
            "schedule": {
                "installed": None,
                "detail": "正在检查每日自动整理状态",
                "daily_limit_label": describe_daily_article_limit(config.rss.daily_article_limit),
            },
            "source": {
                "ok": None,
                "detail": "正在确认订阅是否可读取",
            },
        }

    def get_status_payload(self) -> dict[str, object]:
        config = self.load_config()
        configure_logging(config.output.log_level)
        return self.collect_status(config)

    def build_home_context(self, *, message: str = "", level: str = "info") -> dict[str, object]:
        config = self.load_config()
        configure_logging(config.output.log_level)
        status = self.build_status_placeholder(config)
        briefings = self.list_briefings(config)
        latest_briefing = briefings[0] if briefings else None
        today_text = date.today().isoformat()
        latest_is_today = latest_briefing is not None and latest_briefing.date_text == today_text
        llm_configured = bool(status["llm"]["configured"])

        home_summary = {
            "headline": "今日日报已经成刊" if latest_is_today else "今天的日报还在整理中",
            "detail": (
                f"最近一份日报整理于 {latest_briefing.date_text}"
                if latest_briefing
                else "先看看账号状态，再生成今天的日报"
            ),
            "status_label": "已成刊" if latest_is_today else "整理中",
            "service_summary": "正在确认公众号后台状态",
            "source_summary": "正在确认订阅状态",
            "llm_summary": "AI 模型已经就绪" if llm_configured else "需要摘要时再去设置里补上 AI 模型",
        }

        reminders = [
            {
                "label": "先看看账号状态",
                "detail": "打开公众号后台，确认账号没有进入需要重新登录或暂时休息中",
            },
            {
                "label": "确认订阅已经刷新",
                "detail": "如果有新内容，去后台点一次更新全部",
            },
            {
                "label": "需要摘要时再补模型",
                "detail": "AI 模型没配好时，也可以先生成纯整理版日报",
            },
        ]

        return {
            "page_title": "工作台",
            "message": message,
            "level": level,
            "config": config,
            "status": status,
            "home_summary": home_summary,
            "recent_briefings": briefings[:7],
            "latest_briefing": latest_briefing,
            "today": today_text,
            "today_reminders": reminders,
            "settings_snapshot": {
                "llm": status["llm"]["detail"],
                "schedule": status["schedule"]["detail"],
                "output_dir": _display_path(config.output.briefing_dir),
            },
            "theme_state": _build_theme_state(),
            "about_modal": _build_about_modal(),
            "app_version": __version__,
            "app_version_display": _display_version(__version__),
        }

    def build_settings_context(self, *, message: str = "", level: str = "info") -> dict[str, object]:
        config = self.load_config()
        configure_logging(config.output.log_level)
        status = self.build_status_placeholder(config)
        schedule_hour, schedule_minute = _split_daily_time(config.schedule.daily_time)
        return {
            "page_title": "设置",
            "message": message,
            "level": level,
            "config": config,
            "status": status,
            "yaml_text": _build_redacted_yaml(config),
            "schedule_hour": schedule_hour,
            "schedule_minute": schedule_minute,
            "daily_article_limit": str(normalize_daily_article_limit(config.rss.daily_article_limit)),
            "daily_article_limit_options": _build_daily_limit_options(config.rss.daily_article_limit),
            "briefing_dir_display": _display_path(config.output.briefing_dir),
            "llm_api_key_saved": status["llm"]["api_key_saved"],
            "llm_api_key_source": status["llm"]["api_key_source"],
            "llm_uses_env_api_key": status["llm"]["uses_env_api_key"],
            "theme_state": _build_theme_state(),
            "about_modal": _build_about_modal(),
            "app_version": __version__,
            "app_version_display": _display_version(__version__),
        }

    def collect_status(self, config: AppConfig) -> dict[str, object]:
        service_manager = BundledRSSServiceManager(config.rss_service)
        runtime = service_manager.status_snapshot()
        schedule_installed, schedule_detail = get_schedule_status()
        source_ok, source_detail = self._check_source(config)
        llm_status = _build_llm_status(config)

        return {
            "service": {
                "runtime_ok": runtime.runtime_ok,
                "runtime_detail": runtime.runtime_detail,
                "process_ok": runtime.process_ok,
                "process_detail": runtime.process_detail,
                "web_ok": runtime.web_ok,
                "web_detail": runtime.web_detail,
                "admin_url": runtime.admin_url,
                "feed_url": runtime.feed_url,
            },
            "article_fetch": {
                "http_ok": None,
                "http_detail": "按需检查",
                "browser_ok": None,
                "browser_detail": "按需检查",
            },
            "llm": llm_status,
            "schedule": {
                "installed": schedule_installed,
                "detail": schedule_detail,
                "daily_limit_label": describe_daily_article_limit(config.rss.daily_article_limit),
            },
            "source": {
                "ok": source_ok,
                "detail": source_detail,
            },
        }

    def list_briefings(self, config: AppConfig) -> list[BriefingFile]:
        briefing_dir = Path(config.output.briefing_dir)
        if not briefing_dir.exists():
            return []
        files = [file for file in briefing_dir.glob("*.md") if file.is_file()]
        files = sorted(files, key=lambda item: (item.stat().st_mtime, item.name), reverse=True)

        filtered: list[BriefingFile] = []
        for file in files:
            stem = file.stem
            if len(stem) < 10 or stem[4] != "-" or stem[7] != "-":
                continue
            filtered.append(BriefingFile(name=file.name, date_text=stem, path=str(file.resolve())))
        return filtered[:20]

    def read_briefing(self, briefing_date: str) -> tuple[Path, str]:
        config = self.load_config()
        file_path = Path(config.output.briefing_dir) / f"{briefing_date}.md"
        if not file_path.exists():
            raise FileNotFoundError(f"日报不存在：{file_path}")
        return file_path, file_path.read_text(encoding="utf-8")

    def start_service(self) -> str:
        config = self.load_config()
        return BundledRSSServiceManager(config.rss_service).start()

    def stop_service(self) -> str:
        config = self.load_config()
        return BundledRSSServiceManager(config.rss_service).stop()

    def restart_service(self) -> str:
        config = self.load_config()
        return BundledRSSServiceManager(config.rss_service).restart()

    def open_service_admin(self, *, return_to: str = "") -> str:
        config = self.load_config()
        return BundledRSSServiceManager(config.rss_service).open_admin(return_to=return_to)

    def open_output_dir(self) -> str:
        config = self.load_config()
        output_dir = Path(config.output.briefing_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        open_local_path(output_dir)
        return "已经帮你打开输出目录"

    def pick_output_dir(self) -> tuple[bool, str]:
        config = self.load_config()
        selected = _choose_output_dir(config.output.briefing_dir)
        if not selected:
            return False, "这次没有修改目录"
        config.output.briefing_dir = _normalize_output_dir(selected)
        self.save_config(config)
        return True, "保存目录成功"

    def save_service_settings(self, *, port: int) -> str:
        config = self.load_config()
        config.rss_service.auth_code = ""
        config.rss_service.port = port
        config.rss_service.base_url = f"http://127.0.0.1:{port}"
        if not config.source.url or config.source.url.endswith("/feeds/all.atom"):
            config.source.url = f"{config.rss_service.base_url}/feeds/all.atom"
        self.save_config(config)
        return "公众号服务设置已保存"

    def save_llm(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: int,
        retries: int,
        temperature: float | None = None,
    ) -> tuple[bool, str]:
        config = self.load_config()
        config.llm.base_url = base_url.strip()
        submitted_api_key = api_key.strip()
        if submitted_api_key:
            config.llm.api_key = submitted_api_key
        config.llm.model = model.strip()
        config.llm.timeout_seconds = timeout_seconds
        config.llm.retries = retries
        if temperature is not None:
            config.llm.temperature = temperature
        self.save_config(config)

        ok, detail = OpenAICompatibleSummarizer(config.llm).check_connectivity()
        if ok:
            return True, f"AI 模型配置已保存，连通测试成功：{detail}"
        return False, f"AI 模型配置已保存，连通测试没有通过：{detail}"

    def install_schedule(self, *, run_hour: int, run_minute: int, daily_article_limit: str) -> str:
        config = self.load_config()
        config.schedule.daily_time = _build_daily_time(run_hour, run_minute)
        config.rss.daily_article_limit = normalize_daily_article_limit(daily_article_limit)
        self.save_config(config)
        return install_schedule(config, self.config_path)

    def remove_schedule(self) -> str:
        return remove_schedule()

    def run_now(
        self,
        target_date: date,
        *,
        progress_callback: Callable[[str, str], None] | None = None,
    ) -> tuple[bool, str]:
        config = self.load_config()
        configure_logging(config.output.log_level)
        service_manager = BundledRSSServiceManager(config.rss_service)

        if progress_callback is not None:
            progress_callback("正在检查账号", "正在核对账号状态和可用额度")

        try:
            service_manager.start()
        except Exception as exc:
            return False, f"刷新今日信息失败，请检查账号状态后重试❤️（{exc}）"

        if progress_callback is not None:
            progress_callback("正在刷新订阅", "正在更新今天需要整理的订阅内容")

        try:
            refresh_result = service_manager.refresh_all_feeds()
        except Exception as exc:
            return False, f"刷新今日信息失败，请检查账号状态后重试❤️（{exc}）"

        if not refresh_result.completed:
            detail = refresh_result.detail or refresh_result.reason or "当前没有可用账号，请稍后再试"
            return False, f"刷新今日信息失败，请检查账号状态后重试❤️（{detail}）"

        if progress_callback is not None:
            progress_callback("正在整理日报", "正在把刷新后的内容整理成今天的日报")

        storage = Storage(config.db_path)
        service = ReaderService(
            config=config,
            storage=storage,
            rss_client=RSSClient(config.rss),
            summarizer=OpenAICompatibleSummarizer(config.llm),
            briefing_builder=BriefingBuilder(),
            article_fetcher=ArticleContentFetcher(config.article_fetch, config.rss),
        )
        result = service.run_for_date(target_date, feed_filter=None)
        if result.feed_errors:
            errors = "; ".join(f"{name}: {detail}" for name, detail in result.feed_errors.items())
            return False, f"日报已经生成，但有一部分内容没有成功整理：{errors}"
        return True, f"日报已经生成：{result.briefing_path}"

    def save_advanced_yaml(self, yaml_text: str) -> str:
        parsed = yaml.safe_load(yaml_text) or {}
        existing_config = self.load_config()
        llm_payload = parsed.get("llm")
        if isinstance(llm_payload, dict):
            api_key = str(llm_payload.get("api_key") or "").strip()
            if api_key in {"", LLM_API_KEY_PLACEHOLDER} and existing_config.llm.api_key.strip():
                llm_payload["api_key"] = existing_config.llm.api_key
        config = AppConfig.model_validate(parsed)
        self.save_config(config)
        return "高级设置已保存"

    def _check_source(self, config: AppConfig) -> tuple[bool, str]:
        runtime_feed = config.runtime_feed()
        if not runtime_feed.url:
            return False, "还没有找到可读取的订阅源"
        try:
            ok, detail = RSSClient(config.rss).check_feed(runtime_feed)
        except Exception as exc:
            return False, f"订阅源暂时还没连上：{exc}"
        if ok:
            return True, f"订阅源已经可以读取：{detail}"
        return False, f"订阅源暂时还不可用：{detail}"


def create_app(*, config_path: Path | None = None, backend: DashboardBackend | None = None) -> FastAPI:
    resolved_backend = backend or DashboardBackend(resolve_config_path(config_path))

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            resolved_backend.start_service()
        except Exception as exc:
            LOGGER.warning("Unable to auto-start bundled RSS service: %s", exc)
        yield

    web = FastAPI(title="GZHReader GUI", lifespan=lifespan)
    web.state.backend = resolved_backend
    web.state.run_jobs = RunJobStore()
    templates = _create_templates()
    web.state.templates = templates
    static_dir = _get_static_dir()
    web.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @web.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        LOGGER.exception("Unhandled GUI exception", exc_info=exc)
        return _render_error_response(
            request,
            title="页面暂时打不开",
            description="GZHReader 刚刚在加载这个页面时遇到了问题，请稍后重试，或者看一下日志",
            status_code=500,
        )

    @web.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

    @web.get("/api/status")
    def api_status() -> JSONResponse:
        backend_obj = web.state.backend
        if hasattr(backend_obj, "get_status_payload"):
            return JSONResponse(backend_obj.get_status_payload())
        return JSONResponse(backend_obj.build_home_context()["status"])

    @web.post("/api/run-jobs")
    def create_run_job(target_date: str = Form(...)) -> JSONResponse:
        parsed_date = date.fromisoformat(target_date)
        job_store: RunJobStore = web.state.run_jobs
        job = job_store.create(parsed_date)

        def update_progress(stage: str, detail: str) -> None:
            job_store.update(job.id, stage=stage, detail=detail)

        def worker() -> None:
            try:
                try:
                    ok, detail = web.state.backend.run_now(
                        parsed_date,
                        progress_callback=update_progress,
                    )
                except TypeError:
                    ok, detail = web.state.backend.run_now(parsed_date)

                job_store.update(
                    job.id,
                    status="done" if ok else "error",
                    stage="已经完成" if ok else "已暂停",
                    detail=detail,
                    level="success" if ok else "warning",
                    finished_at=time.time(),
                )
            except Exception as exc:
                job_store.update(
                    job.id,
                    status="error",
                    stage="已暂停",
                    detail=f"生成日报失败：{exc}",
                    level="error",
                    finished_at=time.time(),
                )

        threading.Thread(target=worker, daemon=True).start()
        return JSONResponse({"job_id": job.id})

    @web.get("/api/run-jobs/{job_id}")
    def get_run_job(job_id: str) -> JSONResponse:
        job_store: RunJobStore = web.state.run_jobs
        job = job_store.get(job_id)
        if job is None:
            return JSONResponse({"ok": False, "message": "没有找到这个任务"}, status_code=404)
        return JSONResponse(
            {
                "ok": True,
                "id": job.id,
                "targetDate": job.target_date,
                "status": job.status,
                "stage": job.stage,
                "detail": job.detail,
                "level": job.level,
            }
        )

    @web.get("/favicon.ico")
    def favicon() -> Response:
        return FileResponse(static_dir / "brand" / "gzhreader-icon.svg", media_type="image/svg+xml")

    @web.get("/assets/htmx.min.js")
    def htmx_asset() -> Response:
        return Response(HTMX_MIN_JS, media_type="application/javascript")

    @web.get("/", response_class=HTMLResponse)
    def home(request: Request, message: str = "", level: str = "info"):
        context = request.app.state.backend.build_home_context(message=message, level=level)
        return templates.TemplateResponse(request, "home.html", context)

    @web.get("/settings", response_class=HTMLResponse)
    def settings(request: Request, message: str = "", level: str = "info"):
        context = request.app.state.backend.build_settings_context(message=message, level=level)
        return templates.TemplateResponse(request, "settings.html", context)

    @web.post("/actions/service/start")
    def service_start():
        try:
            detail = web.state.backend.start_service()
            return _redirect("/", detail, "success")
        except Exception as exc:
            return _redirect("/", f"启动服务失败：{exc}", "error")

    @web.post("/actions/service/stop")
    def service_stop():
        try:
            detail = web.state.backend.stop_service()
            return _redirect("/", detail, "success")
        except Exception as exc:
            return _redirect("/", f"停止服务失败：{exc}", "error")

    @web.post("/actions/service/restart")
    def service_restart():
        try:
            detail = web.state.backend.restart_service()
            return _redirect("/settings", detail, "success")
        except Exception as exc:
            return _redirect("/settings", f"重启服务失败：{exc}", "error")

    @web.post("/actions/service/open-admin")
    def service_open_admin(request: Request, return_to: str = Form("")):
        try:
            fallback_return_to = return_to.strip() or request.headers.get("referer", "").strip()
            detail = web.state.backend.open_service_admin(return_to=fallback_return_to)
            return _redirect("/", detail, "success")
        except Exception as exc:
            return _redirect("/", f"打开公众号后台失败：{exc}", "error")

    @web.post("/actions/service/save")
    def save_service_settings(port: int = Form(...)):
        try:
            detail = web.state.backend.save_service_settings(port=port)
            return _redirect("/settings", detail, "success")
        except Exception as exc:
            return _redirect("/settings", f"保存公众号服务设置失败：{exc}", "error")

    @web.post("/actions/save-llm")
    def save_llm(
        base_url: str = Form(...),
        api_key: str = Form(""),
        model: str = Form(...),
        timeout_seconds: int = Form(90),
        retries: int = Form(2),
    ):
        ok, detail = web.state.backend.save_llm(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
        return _redirect("/settings", detail, "success" if ok else "warning")

    @web.post("/actions/pick-output-dir")
    def pick_output_dir():
        try:
            ok, detail = web.state.backend.pick_output_dir()
            return _redirect("/settings", detail, "success" if ok else "info")
        except Exception as exc:
            return _redirect("/settings", f"选择目录失败：{exc}", "error")

    @web.post("/actions/open-output-dir")
    def open_output_dir():
        try:
            detail = web.state.backend.open_output_dir()
            return _redirect("/settings", detail, "success")
        except Exception as exc:
            return _redirect("/settings", f"打开目录失败：{exc}", "error")

    @web.post("/actions/install-schedule")
    def install_schedule_action(
        run_hour: int = Form(...),
        run_minute: int = Form(...),
        daily_article_limit: str = Form(...),
    ):
        try:
            detail = web.state.backend.install_schedule(
                run_hour=run_hour,
                run_minute=run_minute,
                daily_article_limit=daily_article_limit,
            )
            return _redirect("/settings", detail, "success")
        except Exception as exc:
            return _redirect("/settings", f"安装自动运行失败：{exc}", "error")

    @web.post("/actions/remove-schedule")
    def remove_schedule_action():
        try:
            detail = web.state.backend.remove_schedule()
            return _redirect("/settings", detail, "success")
        except Exception as exc:
            return _redirect("/settings", f"移除自动运行失败：{exc}", "error")

    @web.post("/actions/run-now")
    def run_now(target_date: str = Form(...)):
        try:
            parsed_date = date.fromisoformat(target_date)
            ok, detail = web.state.backend.run_now(parsed_date)
            return _redirect("/", detail, "success" if ok else "warning")
        except Exception as exc:
            return _redirect("/", f"生成日报失败：{exc}", "error")

    @web.post("/actions/save-advanced")
    def save_advanced(yaml_text: str = Form(...)):
        try:
            detail = web.state.backend.save_advanced_yaml(yaml_text)
            return _redirect("/settings", detail, "success")
        except Exception as exc:
            return _redirect("/settings", f"保存高级设置失败：{exc}", "error")

    @web.get("/briefings/latest")
    def latest_briefing():
        backend_obj = web.state.backend
        if hasattr(backend_obj, "load_config") and hasattr(backend_obj, "list_briefings"):
            config = backend_obj.load_config()
            briefings = backend_obj.list_briefings(config)
        else:
            briefings = backend_obj.build_home_context()["recent_briefings"]
        if not briefings:
            return _redirect("/", "还没有生成过日报", "warning")
        latest = briefings[0]
        return RedirectResponse(url=f"/briefings/{latest.date_text}", status_code=303)

    @web.get("/briefings/{briefing_date}", response_class=HTMLResponse)
    def view_briefing(request: Request, briefing_date: str):
        try:
            file_path, content = web.state.backend.read_briefing(briefing_date)
        except Exception as exc:
            return _redirect("/", f"打开日报失败：{exc}", "error")
        return templates.TemplateResponse(
            request,
            "briefing.html",
            {
                "page_title": briefing_date,
                "briefing_date": briefing_date,
                "briefing_path": str(file_path),
                "content": content,
                "briefing_html": _render_markdown(content),
                "theme_state": _build_theme_state(),
                "about_modal": _build_about_modal(),
                "app_version": __version__,
                "app_version_display": _display_version(__version__),
            },
        )

    return web


def _redirect(path: str, message: str, level: str) -> RedirectResponse:
    query = urlencode({"message": message, "level": level})
    return RedirectResponse(url=f"{path}?{query}", status_code=303)


def _render_error_response(request: Request, *, title: str, description: str, status_code: int = 500) -> HTMLResponse:
    log_path = get_runtime_paths().logs_dir / "gzhreader.log"
    safe_title = escape(title)
    safe_description = escape(description)
    safe_request_path = escape(str(request.url.path))
    safe_log_path = escape(str(log_path))
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    body {{ margin: 0; font-family: "Segoe UI", "Noto Sans SC", sans-serif; background: #f5efe5; color: #1d1813; }}
    .shell {{ max-width: 760px; margin: 0 auto; padding: 48px 20px; }}
    .card {{ background: #fffef9; border: 1px solid #d8cdbf; border-radius: 28px; padding: 28px; box-shadow: 0 22px 48px rgba(60, 45, 28, 0.08); }}
    h1 {{ margin: 0 0 12px; font-size: 30px; }}
    p {{ line-height: 1.8; color: #564b41; }}
    .meta {{ margin-top: 18px; padding: 14px 16px; border-radius: 18px; background: #f7f1e7; border: 1px solid #e3d7c7; color: #5a4635; line-height: 1.8; overflow-wrap: anywhere; }}
    .actions {{ display: flex; gap: 12px; flex-wrap: wrap; margin-top: 22px; }}
    .button {{ display: inline-flex; align-items: center; justify-content: center; min-height: 44px; padding: 0 16px; border-radius: 14px; text-decoration: none; font-weight: 600; }}
    .button.primary {{ background: #3b2f28; color: #fffef9; }}
    .button.secondary {{ background: #efe5d8; color: #3d3028; }}
  </style>
</head>
<body>
  <div class="shell">
    <div class="card">
      <h1>{safe_title}</h1>
      <p>{safe_description}</p>
      <div class="meta">当前页面：{safe_request_path}<br>日志文件：{safe_log_path}</div>
      <div class="actions">
        <a class="button primary" href="/">返回首页</a>
        <a class="button secondary" href="javascript:window.location.reload()">重新加载</a>
      </div>
    </div>
  </div>
</body>
</html>
"""
    return HTMLResponse(html, status_code=status_code)
