from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from html import escape
import logging
from pathlib import Path
from urllib.parse import urlencode

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
        "tagline": "把公众号阅读整理成本地日报的阅读工作台",
        "motivation_title": "开发动机",
        "motivation_text": (
            "GZHReader 想把每天零散掠过的公众号阅读，整理成一份可以安静回看的本地日报。"
            "它不强调配置感，而是希望像一张编辑台，把阅读、摘要和归档收拢到同一个地方。"
        ),
        "feedback_title": "反馈",
        "feedback_text": "关于页入口先保留到这里，后续可以继续补充反馈方式、支持入口和更多作者信息。",
        "feedback_url": "",
        "feedback_label": "",
        "support_title": "支持项目",
        "support_text": "如果这个工作台帮你省下了每天整理公众号阅读的时间，它就已经完成了最重要的价值。",
        "support_url": "",
        "support_label": "",
        "author_title": "关于作者",
        "author_name": "GZHReader 作者",
        "author_lines": [
            "专注把公众号阅读、摘要与归档做成更安静的一体化体验",
            "本轮入口先做成可复用的 about 弹窗，后续内容可以继续细化",
        ],
        "author_url": "",
        "author_label": "",
        "footer_lines": [
            "本地优先 · 每日简报 · 公众号阅读工作台",
            "为能长期回看的阅读流保留一个更稳的桌面入口",
        ],
    }


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


def _build_llm_status(config: AppConfig) -> dict[str, object]:
    api_key, api_key_source = resolve_api_key(config.llm)
    configured = bool(config.llm.base_url.strip() and config.llm.model.strip() and api_key)

    if configured:
        if api_key_source == "config":
            detail = "AI 摘要配置已保存，本地密钥将继续用于生成摘要。"
        else:
            detail = "正在使用 OPENAI_API_KEY 环境变量生成摘要。"
    else:
        detail = "还没有完成 AI 摘要配置。"

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
        raise RuntimeError("当前环境无法打开系统目录选择器，请手动输入目录路径。") from exc

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

    def build_home_context(self, *, message: str = "", level: str = "info") -> dict[str, object]:
        config = self.load_config()
        configure_logging(config.output.log_level)
        status = self.collect_status(config)
        briefings = self.list_briefings(config)
        latest_briefing = briefings[0] if briefings else None
        today_text = date.today().isoformat()
        latest_is_today = latest_briefing is not None and latest_briefing.date_text == today_text

        home_summary = {
            "headline": "今日日报已经成刊" if latest_is_today else "今天的日报还在整理中",
            "detail": (
                f"最近一次日报：{latest_briefing.date_text}"
                if latest_briefing
                else "启动公众号服务后，点击“立即生成今天”即可开始整理今天的阅读流。"
            ),
            "status_label": "已成刊" if latest_is_today else "整理中",
        }
        return {
            "page_title": "工作台",
            "message": message,
            "level": level,
            "config": config,
            "status": status,
            "home_summary": home_summary,
            "quick_actions": [
                {"label": "立即生成今天", "action": "/actions/run-now"},
                {"label": "打开公众号后台", "action": "/actions/service/open-admin"},
                {"label": "进入设置", "href": "/settings"},
            ],
            "recent_briefings": briefings[:7],
            "latest_briefing": latest_briefing,
            "today": today_text,
            "settings_snapshot": {
                "llm": status["llm"]["detail"],
                "schedule": status["schedule"]["detail"],
                "output_dir": _display_path(config.output.briefing_dir),
            },
            "theme_state": {
                "default": "system",
                "options": [
                    {"value": "system", "label": "跟随系统"},
                    {"value": "light", "label": "浅色"},
                    {"value": "dark", "label": "深色"},
                ],
            },
            "about_modal": _build_about_modal(),
            "app_version": __version__,
        }

    def build_settings_context(self, *, message: str = "", level: str = "info") -> dict[str, object]:
        config = self.load_config()
        configure_logging(config.output.log_level)
        status = self.collect_status(config)
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
            "theme_state": {
                "default": "system",
                "options": [
                    {"value": "system", "label": "跟随系统"},
                    {"value": "light", "label": "浅色"},
                    {"value": "dark", "label": "深色"},
                ],
            },
            "about_modal": _build_about_modal(),
            "app_version": __version__,
        }

    def collect_status(self, config: AppConfig) -> dict[str, object]:
        service_manager = BundledRSSServiceManager(config.rss_service)
        runtime = service_manager.status_snapshot()
        article_fetcher = ArticleContentFetcher(config.article_fetch, config.rss)
        http_ok, http_detail = article_fetcher.check_http_runtime()
        browser_ok, browser_detail = article_fetcher.check_browser_runtime()
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
                "http_ok": http_ok,
                "http_detail": http_detail,
                "browser_ok": browser_ok,
                "browser_detail": browser_detail,
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

    def open_service_admin(self) -> str:
        config = self.load_config()
        return BundledRSSServiceManager(config.rss_service).open_admin()

    def open_output_dir(self) -> str:
        config = self.load_config()
        output_dir = Path(config.output.briefing_dir).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        open_local_path(output_dir)
        return f"已尝试打开输出目录：{output_dir}"

    def pick_output_dir(self) -> tuple[bool, str]:
        config = self.load_config()
        selected = _choose_output_dir(config.output.briefing_dir)
        if not selected:
            return False, "已取消目录选择。"
        config.output.briefing_dir = _normalize_output_dir(selected)
        self.save_config(config)
        return True, f"输出目录已更新：{config.output.briefing_dir}"

    def save_output_dir(self, briefing_dir: str) -> str:
        config = self.load_config()
        config.output.briefing_dir = _normalize_output_dir(briefing_dir)
        self.save_config(config)
        return f"输出目录已保存：{config.output.briefing_dir}"

    def save_service_settings(self, *, port: int) -> str:
        config = self.load_config()
        config.rss_service.auth_code = ""
        config.rss_service.port = port
        config.rss_service.base_url = f"http://127.0.0.1:{port}"
        if not config.source.url or config.source.url.endswith("/feeds/all.atom"):
            config.source.url = f"{config.rss_service.base_url}/feeds/all.atom"
        self.save_config(config)
        return "公众号服务设置已保存。"

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
            return True, f"AI 摘要配置已保存并测试成功：{detail}"
        return False, f"AI 摘要配置已保存，但测试失败：{detail}"

    def save_schedule(self, *, run_hour: int, run_minute: int, daily_article_limit: str) -> str:
        config = self.load_config()
        config.schedule.daily_time = _build_daily_time(run_hour, run_minute)
        config.rss.daily_article_limit = normalize_daily_article_limit(daily_article_limit)
        self.save_config(config)
        return "自动运行时间与文章范围已保存。"

    def install_schedule(self, *, run_hour: int, run_minute: int, daily_article_limit: str) -> str:
        config = self.load_config()
        config.schedule.daily_time = _build_daily_time(run_hour, run_minute)
        config.rss.daily_article_limit = normalize_daily_article_limit(daily_article_limit)
        self.save_config(config)
        return install_schedule(config, self.config_path)

    def remove_schedule(self) -> str:
        return remove_schedule()

    def run_now(self, target_date: date) -> tuple[bool, str]:
        config = self.load_config()
        configure_logging(config.output.log_level)
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
            return False, (
                f"运行完成，但存在错误。run={result.run_key} collected={result.collected} "
                f"inserted={result.inserted} summarized={result.summarized}；{errors}"
            )
        return True, (
            f"运行完成。run={result.run_key} collected={result.collected} inserted={result.inserted} "
            f"summarized={result.summarized} briefing={result.briefing_path}"
        )

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
        return "高级 YAML 配置已保存。"

    def _check_source(self, config: AppConfig) -> tuple[bool, str]:
        runtime_feed = config.runtime_feed()
        if not runtime_feed.url:
            return False, "缺少 source.url。"
        try:
            ok, detail = RSSClient(config.rss).check_feed(runtime_feed)
        except Exception as exc:
            return False, f"聚合源检查失败：{exc}"
        if ok:
            return True, f"聚合源可读取：{runtime_feed.url}；{detail}"
        return False, f"当前聚合源还不可用：{detail}"


def create_app(*, config_path: Path | None = None, backend: DashboardBackend | None = None) -> FastAPI:
    resolved_backend = backend or DashboardBackend(resolve_config_path(config_path))
    web = FastAPI(title="GZHReader GUI")
    web.state.backend = resolved_backend
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
            description="GZHReader 刚刚在加载这个页面时遇到了问题，请稍后重试或查看日志。",
            status_code=500,
        )

    @web.get("/healthz")
    def healthz() -> JSONResponse:
        return JSONResponse({"ok": True})

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
    def service_open_admin():
        try:
            detail = web.state.backend.open_service_admin()
            return _redirect("/", detail, "success")
        except Exception as exc:
            return _redirect("/", f"打开后台失败：{exc}", "error")

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
        api_key: str = Form(...),
        model: str = Form(...),
        timeout_seconds: int = Form(...),
        retries: int = Form(...),
    ):
        ok, detail = web.state.backend.save_llm(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
        return _redirect("/settings", detail, "success" if ok else "warning")

    @web.post("/actions/save-output-dir")
    def save_output_dir(briefing_dir: str = Form(...)):
        try:
            detail = web.state.backend.save_output_dir(briefing_dir)
            return _redirect("/settings", detail, "success")
        except Exception as exc:
            return _redirect("/settings", f"保存输出目录失败：{exc}", "error")

    @web.post("/actions/pick-output-dir")
    def pick_output_dir():
        try:
            ok, detail = web.state.backend.pick_output_dir()
            return _redirect("/settings", detail, "success" if ok else "info")
        except Exception as exc:
            return _redirect("/settings", f"选择输出目录失败：{exc}", "error")

    @web.post("/actions/open-output-dir")
    def open_output_dir():
        try:
            detail = web.state.backend.open_output_dir()
            return _redirect("/", detail, "success")
        except Exception as exc:
            return _redirect("/", f"打开输出目录失败：{exc}", "error")

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

    @web.post("/actions/save-schedule")
    def save_schedule_action(
        run_hour: int = Form(...),
        run_minute: int = Form(...),
        daily_article_limit: str = Form(...),
    ):
        try:
            detail = web.state.backend.save_schedule(
                run_hour=run_hour,
                run_minute=run_minute,
                daily_article_limit=daily_article_limit,
            )
            return _redirect("/settings", detail, "success")
        except Exception as exc:
            return _redirect("/settings", f"保存自动运行设置失败：{exc}", "error")

    @web.post("/actions/run-now")
    def run_now(target_date: str = Form(...)):
        try:
            parsed_date = date.fromisoformat(target_date)
            ok, detail = web.state.backend.run_now(parsed_date)
            return _redirect("/", detail, "success" if ok else "warning")
        except Exception as exc:
            return _redirect("/", f"立即运行失败：{exc}", "error")

    @web.post("/actions/save-advanced")
    def save_advanced(yaml_text: str = Form(...)):
        try:
            detail = web.state.backend.save_advanced_yaml(yaml_text)
            return _redirect("/settings", detail, "success")
        except Exception as exc:
            return _redirect("/settings", f"保存高级配置失败：{exc}", "error")

    @web.get("/briefings/latest")
    def latest_briefing():
        context = web.state.backend.build_home_context()
        briefings = context["recent_briefings"]
        if not briefings:
            return _redirect("/", "还没有生成过日报。", "warning")
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
                "theme_state": _build_theme_state(),
                "about_modal": _build_about_modal(),
                "app_version": __version__,
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
