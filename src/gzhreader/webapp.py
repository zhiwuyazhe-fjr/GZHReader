from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import logging
import re
from urllib.parse import urlencode
import webbrowser
from html import escape

import yaml
from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
from .rss_client import RSSClient
from .runtime_paths import get_resource_root, get_runtime_paths, resolve_config_path
from .scheduler import get_schedule_status, install_schedule, remove_schedule
from .service import ReaderService
from .storage import Storage
from .summarizer import OpenAICompatibleSummarizer, resolve_api_key
from .wewe_rss import WeWeRSSManager

LOGGER = logging.getLogger(__name__)

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


def _get_template_dir() -> Path:
    return _resolve_resource_dir("templates")


def _get_static_dir() -> Path:
    return _resolve_resource_dir("static")


def _create_templates() -> Jinja2Templates:
    return Jinja2Templates(directory=str(_get_template_dir()))


DOCKER_DOWNLOAD_URL = "https://docs.docker.com/desktop/setup/install/windows-install/"
DOCKER_INSTALL_URL = "https://docs.docker.com/desktop/"
BRIEFING_FILENAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:_\d+)?\.md$")
LLM_API_KEY_PLACEHOLDER = "__GZHREADER_KEEP_EXISTING_API_KEY__"
TERMINAL_NOTICE = "运行某些步骤时，程序可能会短暂打开终端或系统窗口。这是正常现象，不需要手动关闭，等待当前步骤完成即可。"


def _build_llm_status(config: AppConfig) -> dict[str, object]:
    api_key, api_key_source = resolve_api_key(config.llm)
    has_endpoint = bool(config.llm.base_url.strip() and config.llm.model.strip())
    api_key_saved = bool(config.llm.api_key.strip())
    configured = bool(has_endpoint and api_key)

    if configured:
        if api_key_source == "config":
            detail = "已在本地配置中保存 base_url / model / api_key。保存时会自动测试连通性。"
        else:
            detail = "已检测到 OPENAI_API_KEY 环境变量。当前界面不会回显该密钥，但运行和连通性测试会继续使用它。"
    else:
        detail = "请先填写 base_url / model，并提供本地 api_key 或 OPENAI_API_KEY。"

    return {
        "configured": configured,
        "detail": detail,
        "api_key_source": api_key_source,
        "api_key_saved": api_key_saved,
        "uses_env_api_key": api_key_source == "env",
    }


def _build_redacted_yaml(config: AppConfig) -> str:
    payload = config.model_dump(mode="json", exclude={"feeds"})
    llm_payload = payload.get("llm")
    if isinstance(llm_payload, dict) and llm_payload.get("api_key"):
        llm_payload["api_key"] = LLM_API_KEY_PLACEHOLDER
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


@dataclass(slots=True)
class BriefingFile:
    name: str
    date_text: str
    path: str


def _build_action_result(
    *,
    scope: str,
    title: str,
    message: str,
    level: str,
    step_id: str | None = None,
) -> dict[str, str]:
    result = {
        "scope": scope,
        "title": title,
        "message": message,
        "level": level,
    }
    if step_id is not None:
        result["step_id"] = step_id
    return result


class DashboardBackend:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    def load_config(self) -> AppConfig:
        return ensure_config(self.config_path)

    def save_config(self, config: AppConfig) -> None:
        save_config(config, self.config_path)

    def build_dashboard_context(
        self,
        *,
        message: str = "",
        level: str = "info",
        action_result: dict | None = None,
    ) -> dict:
        config = self.load_config()
        configure_logging(config.output.log_level)
        briefings = self.list_briefings(config)
        latest_briefing = briefings[0] if briefings else None
        status = self.collect_status(config)
        llm_status = _build_llm_status(config)
        wizard_steps = self.build_wizard_steps(config, status, latest_briefing, action_result=action_result)
        docker_setup = self.build_docker_setup(status)
        schedule_hour, schedule_minute = _split_daily_time(config.schedule.daily_time)
        daily_article_limit = normalize_daily_article_limit(config.rss.daily_article_limit)
        return {
            "config": config,
            "config_path": str(self.config_path.resolve()),
            "message": message,
            "level": level,
            "status": status,
            "wizard_steps": wizard_steps,
            "briefings": briefings,
            "latest_briefing": latest_briefing,
            "today": date.today().isoformat(),
            "yaml_text": _build_redacted_yaml(config),
            "schedule_hour": schedule_hour,
            "schedule_minute": schedule_minute,
            "daily_article_limit": str(daily_article_limit),
            "daily_article_limit_options": _build_daily_limit_options(daily_article_limit),
            "briefing_dir_display": _display_path(config.output.briefing_dir),
            "docker_blocked": docker_setup["blocked"],
            "docker_setup": docker_setup,
            "llm_api_key_saved": llm_status["api_key_saved"],
            "llm_api_key_source": llm_status["api_key_source"],
            "llm_uses_env_api_key": llm_status["uses_env_api_key"],
            "advanced_feedback": action_result if action_result and action_result.get("scope") == "advanced" else None,
            "terminal_notice": TERMINAL_NOTICE,
        }

    def collect_status(self, config: AppConfig) -> dict:
        manager = WeWeRSSManager(config.wewe_rss)
        manager.ensure_scaffold(force=False)
        runtime = manager.status_snapshot()

        article_fetcher = ArticleContentFetcher(config.article_fetch, config.rss)
        http_ok, http_detail = article_fetcher.check_http_runtime()
        browser_ok, browser_detail = article_fetcher.check_browser_runtime()

        schedule_ok, schedule_detail = get_schedule_status()
        source_ok, source_detail = self._check_source(config)

        llm_status = _build_llm_status(config)
        llm_configured = bool(llm_status["configured"])
        llm_detail = str(llm_status["detail"])

        environment_ready = runtime.docker_ok and http_ok and browser_ok
        rss_service_ready = runtime.app_ok and runtime.mysql_ok and runtime.web_ok

        return {
            "docker_ok": runtime.docker_ok,
            "docker_detail": runtime.docker_detail,
            "environment_ready": environment_ready,
            "environment_items": [
                {"label": "Docker Desktop", "ok": runtime.docker_ok, "detail": runtime.docker_detail},
                {"label": "HTTP 正文抓取", "ok": http_ok, "detail": http_detail},
                {"label": "浏览器正文抓取", "ok": browser_ok, "detail": browser_detail},
            ],
            "rss_service_ready": rss_service_ready,
            "rss_service_items": [
                {"label": "wewe-rss-app", "ok": runtime.app_ok, "detail": runtime.app_detail},
                {"label": "mysql", "ok": runtime.mysql_ok, "detail": runtime.mysql_detail},
                {"label": "wewe-rss Web 后台", "ok": runtime.web_ok, "detail": runtime.web_detail},
            ],
            "source_ok": source_ok,
            "source_detail": source_detail,
            "llm_configured": llm_configured,
            "llm_detail": llm_detail,
            "schedule_installed": schedule_ok,
            "schedule_detail": schedule_detail,
            "daily_article_limit_label": describe_daily_article_limit(config.rss.daily_article_limit),
            "environment": [
                {"label": "Docker Desktop", "ok": runtime.docker_ok, "detail": runtime.docker_detail},
                {"label": "HTTP 正文抓取", "ok": http_ok, "detail": http_detail},
                {"label": "浏览器正文抓取", "ok": browser_ok, "detail": browser_detail},
            ],
            "rss_service": [
                {"label": "wewe-rss-app", "ok": runtime.app_ok, "detail": runtime.app_detail},
                {"label": "mysql", "ok": runtime.mysql_ok, "detail": runtime.mysql_detail},
                {"label": "wewe-rss Web 后台", "ok": runtime.web_ok, "detail": runtime.web_detail},
            ],
        }

    def build_docker_setup(self, status: dict) -> dict:
        if status["docker_ok"]:
            return {
                "blocked": False,
                "status_title": "Docker Desktop 已就绪",
                "status_badge": "已通过",
                "description": "Docker 已可用，现在可以继续后续向导步骤。",
                "next_step": "继续执行第 2 步，启动 RSS 服务。",
                "detail": status["docker_detail"],
                "download_url": DOCKER_DOWNLOAD_URL,
                "install_url": DOCKER_INSTALL_URL,
            }

        detail = status["docker_detail"]
        title, description, next_step = _classify_docker_problem(detail)
        return {
            "blocked": True,
            "status_title": title,
            "status_badge": "需先处理",
            "description": description,
            "next_step": next_step,
            "detail": detail,
            "download_url": DOCKER_DOWNLOAD_URL,
            "install_url": DOCKER_INSTALL_URL,
        }

    def build_wizard_steps(
        self,
        config: AppConfig,
        status: dict,
        latest_briefing: BriefingFile | None,
        *,
        action_result: dict | None = None,
    ) -> list[dict]:
        output_dir_display = _display_path(config.output.briefing_dir)
        raw_steps = [
            {
                "id": "environment",
                "number": 1,
                "title": "检查环境",
                "summary": "确认 Docker Desktop 已启动，并检查正文抓取依赖是否可用。",
                "detail": "；".join(item["detail"] for item in status["environment_items"]),
                "done": status["environment_ready"],
            },
            {
                "id": "rss_service",
                "number": 2,
                "title": "启动 RSS 服务",
                "summary": "启动 wewe-rss-app 和 mysql 两个容器。",
                "detail": "；".join(item["detail"] for item in status["rss_service_items"]),
                "done": status["rss_service_ready"],
            },
            {
                "id": "subscription",
                "number": 3,
                "title": "登录并订阅公众号",
                "summary": "打开 wewe-rss 后台，扫码登录并添加公众号订阅。",
                "detail": status["source_detail"],
                "done": status["source_ok"],
            },
            {
                "id": "llm",
                "number": 4,
                "title": "配置并测试 LLM",
                "summary": "填写大模型接口信息，保存时自动做一次连接测试。",
                "detail": status["llm_detail"],
                "done": status["llm_configured"],
            },
            {
                "id": "output_dir",
                "number": 5,
                "title": "选择生成结果保存位置",
                "summary": "先决定 Markdown 日报要保存到哪个目录。",
                "detail": f"当前保存目录：{output_dir_display}",
                "done": bool(str(config.output.briefing_dir).strip()),
            },
            {
                "id": "schedule",
                "number": 6,
                "title": "设置每日任务",
                "summary": "按本机时间安装到 Windows 计划任务中。",
                "detail": f"{status['schedule_detail']}；当前设置：{status['daily_article_limit_label']}",
                "done": status["schedule_installed"],
            },
            {
                "id": "run_once",
                "number": 7,
                "title": "立即运行一次测试",
                "summary": "读取聚合源、补抓正文、总结并生成 Markdown。",
                "detail": f"最近一次已生成：{latest_briefing.date_text}" if latest_briefing else "还没有生成过日报。",
                "done": latest_briefing is not None,
            },
            {
                "id": "briefing",
                "number": 8,
                "title": "查看生成结果",
                "summary": "最终产物只保留 Markdown 日报。",
                "detail": latest_briefing.path if latest_briefing else f"结果文件会出现在 {output_dir_display}/YYYY-MM-DD.md。",
                "done": latest_briefing is not None,
            },
        ]

        current_index = next((index for index, step in enumerate(raw_steps) if not step["done"]), len(raw_steps) - 1)
        unlocked = True
        steps: list[dict] = []
        for index, step in enumerate(raw_steps):
            locked = not unlocked
            current = index == current_index and not locked
            action_feedback = None
            if action_result and action_result.get("scope") == "wizard" and action_result.get("step_id") == step["id"]:
                action_feedback = action_result
            steps.append({**step, "locked": locked, "current": current, "action_feedback": action_feedback})
            if not step["done"]:
                unlocked = False
        return steps


    def list_briefings(self, config: AppConfig) -> list[BriefingFile]:
        briefing_dir = Path(config.output.briefing_dir)
        if not briefing_dir.exists():
            return []
        files = [
            file
            for file in briefing_dir.glob("*.md")
            if file.is_file() and BRIEFING_FILENAME_RE.match(file.name)
        ]
        files = sorted(files, key=lambda item: (item.stat().st_mtime, item.name), reverse=True)
        return [BriefingFile(name=file.name, date_text=file.stem, path=str(file.resolve())) for file in files[:20]]

    def read_briefing(self, briefing_date: str) -> tuple[Path, str]:
        config = self.load_config()
        file_path = Path(config.output.briefing_dir) / f"{briefing_date}.md"
        if not file_path.exists():
            raise FileNotFoundError(f"日报不存在：{file_path}")
        return file_path, file_path.read_text(encoding="utf-8")

    def is_docker_ready(self) -> tuple[bool, str]:
        config = self.load_config()
        return WeWeRSSManager(config.wewe_rss).check_docker()

    def start_rss(self) -> str:
        config = self.load_config()
        return WeWeRSSManager(config.wewe_rss).up()

    def stop_rss(self) -> str:
        config = self.load_config()
        return WeWeRSSManager(config.wewe_rss).down()

    def open_wewe_rss(self) -> str:
        config = self.load_config()
        webbrowser.open(config.wewe_rss.base_url)
        return f"已尝试打开 {config.wewe_rss.base_url}。请在后台完成扫码登录和公众号订阅，然后回到这里点“刷新状态”。"

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
            return True, f"LLM 配置已保存并测试成功：{detail}"
        return False, f"LLM 配置已保存，但测试失败：{detail}"

    def save_schedule(self, *, run_hour: int, run_minute: int, daily_article_limit: str) -> str:
        config = self.load_config()
        config.schedule.daily_time = _build_daily_time(run_hour, run_minute)
        config.rss.daily_article_limit = normalize_daily_article_limit(daily_article_limit)
        self.save_config(config)
        return f"计划任务时间已保存，当前文章数量设置：{describe_daily_article_limit(config.rss.daily_article_limit)}。"

    def install_schedule(self, *, run_hour: int, run_minute: int, daily_article_limit: str) -> str:
        config = self.load_config()
        config.schedule.daily_time = _build_daily_time(run_hour, run_minute)
        config.rss.daily_article_limit = normalize_daily_article_limit(daily_article_limit)
        self.save_config(config)
        detail = install_schedule(config, self.config_path)
        return f"{detail}；当前文章数量设置：{describe_daily_article_limit(config.rss.daily_article_limit)}。"

    def save_output_dir(self, briefing_dir: str) -> str:
        config = self.load_config()
        normalized_dir = _normalize_output_dir(briefing_dir)
        Path(normalized_dir).mkdir(parents=True, exist_ok=True)
        config.output.briefing_dir = normalized_dir
        self.save_config(config)
        return f"Markdown 保存目录已更新：{normalized_dir}"

    def pick_output_dir(self) -> tuple[bool, str]:
        config = self.load_config()
        selected_dir = _choose_output_dir(config.output.briefing_dir)
        if not selected_dir:
            return False, "你还没有选择新目录，已保留当前 Markdown 保存位置。"
        normalized_dir = _normalize_output_dir(selected_dir)
        Path(normalized_dir).mkdir(parents=True, exist_ok=True)
        config.output.briefing_dir = normalized_dir
        self.save_config(config)
        return True, f"Markdown 保存目录已更新：{normalized_dir}"

    def remove_schedule(self) -> str:
        return remove_schedule()

    def run_now(self, target_date: date) -> tuple[bool, str]:
        config = self.load_config()
        configure_logging(config.output.log_level)
        service = ReaderService(
            config=config,
            storage=Storage(config.db_path),
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
        return False, f"请先在 wewe-rss 完成扫码登录和订阅后再刷新：{detail}"


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
            description=(
                "GZHReader 刚刚在加载这个页面时遇到了问题。你可以先返回首页再试一次；"
                "如果问题持续存在，请把日志文件发给我。"
            ),
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
    def dashboard(request: Request, message: str = "", level: str = "info"):
        context = request.app.state.backend.build_dashboard_context(message=message, level=level)
        return templates.TemplateResponse(request, "dashboard.html", context)

    @web.get("/partials/flash", response_class=HTMLResponse)
    def flash_partial(request: Request, message: str = "", level: str = "info"):
        return templates.TemplateResponse(request, "partials/flash.html", {"message": message, "level": level})

    @web.get("/partials/wizard", response_class=HTMLResponse)
    def wizard_partial(request: Request):
        context = request.app.state.backend.build_dashboard_context()
        return templates.TemplateResponse(request, "partials/wizard.html", context)

    @web.get("/partials/docker-setup", response_class=HTMLResponse)
    def docker_setup_partial(request: Request):
        context = request.app.state.backend.build_dashboard_context()
        return templates.TemplateResponse(request, "partials/docker_setup.html", context)

    @web.get("/partials/main-content", response_class=HTMLResponse)
    def main_content_partial(request: Request):
        context = request.app.state.backend.build_dashboard_context()
        return templates.TemplateResponse(request, "partials/main_content.html", context)

    @web.get("/partials/briefings", response_class=HTMLResponse)
    def briefings_partial(request: Request):
        context = request.app.state.backend.build_dashboard_context()
        return templates.TemplateResponse(request, "partials/briefings.html", context)

    @web.get("/partials/advanced", response_class=HTMLResponse)
    def advanced_partial(request: Request):
        context = request.app.state.backend.build_dashboard_context()
        return templates.TemplateResponse(request, "partials/advanced.html", context)

    @web.get("/actions/refresh")
    def refresh(request: Request):
        return _after_action(request, "状态已刷新。", "info", step_id="environment", action_title="重新检查环境")

    @web.post("/actions/start-rss")
    def start_rss(request: Request):
        blocked = _block_if_docker_unavailable(request, "启动 RSS 服务", step_id="rss_service")
        if blocked is not None:
            return blocked
        try:
            detail = request.app.state.backend.start_rss()
            return _after_action(request, f"RSS 服务已启动：{detail}", "success", step_id="rss_service", action_title="启动 RSS 服务")
        except Exception as exc:
            return _after_action(request, f"启动 RSS 服务失败：{exc}", "error", step_id="rss_service", action_title="启动 RSS 服务")

    @web.post("/actions/stop-rss")
    def stop_rss(request: Request):
        blocked = _block_if_docker_unavailable(request, "停止 RSS 服务", step_id="rss_service")
        if blocked is not None:
            return blocked
        try:
            detail = request.app.state.backend.stop_rss()
            return _after_action(request, f"RSS 服务已停止：{detail}", "success", step_id="rss_service", action_title="停止 RSS 服务")
        except Exception as exc:
            return _after_action(request, f"停止 RSS 服务失败：{exc}", "error", step_id="rss_service", action_title="停止 RSS 服务")

    @web.post("/actions/open-wewe-rss")
    def open_wewe_rss(request: Request):
        blocked = _block_if_docker_unavailable(request, "打开 wewe-rss", step_id="subscription")
        if blocked is not None:
            return blocked
        try:
            detail = request.app.state.backend.open_wewe_rss()
            return _after_action(request, detail, "success", step_id="subscription", action_title="打开 wewe-rss 后台")
        except Exception as exc:
            return _after_action(request, f"打开 wewe-rss 后台失败：{exc}", "error", step_id="subscription", action_title="打开 wewe-rss 后台")

    @web.post("/actions/save-llm")
    def save_llm(
        request: Request,
        base_url: str = Form(...),
        api_key: str = Form(...),
        model: str = Form(...),
        timeout_seconds: int = Form(...),
        retries: int = Form(...),
    ):
        blocked = _block_if_docker_unavailable(request, "保存 LLM 配置", step_id="llm")
        if blocked is not None:
            return blocked
        ok, detail = request.app.state.backend.save_llm(
            base_url=base_url,
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
            retries=retries,
        )
        return _after_action(request, detail, "success" if ok else "warning", step_id="llm", action_title="保存并测试 LLM")

    @web.post("/actions/save-schedule")
    def save_schedule(
        request: Request,
        run_hour: int = Form(...),
        run_minute: int = Form(...),
        daily_article_limit: str = Form(...),
    ):
        blocked = _block_if_docker_unavailable(request, "保存计划任务", step_id="schedule")
        if blocked is not None:
            return blocked
        try:
            detail = request.app.state.backend.save_schedule(
                run_hour=run_hour,
                run_minute=run_minute,
                daily_article_limit=daily_article_limit,
            )
            return _after_action(request, detail, "success", step_id="schedule", action_title="保存计划任务设置")
        except Exception as exc:
            return _after_action(request, f"保存计划任务配置失败：{exc}", "error", step_id="schedule", action_title="保存计划任务设置")

    @web.post("/actions/install-schedule")
    def install_schedule_action(
        request: Request,
        run_hour: int = Form(...),
        run_minute: int = Form(...),
        daily_article_limit: str = Form(...),
    ):
        blocked = _block_if_docker_unavailable(request, "安装计划任务", step_id="schedule")
        if blocked is not None:
            return blocked
        try:
            detail = request.app.state.backend.install_schedule(
                run_hour=run_hour,
                run_minute=run_minute,
                daily_article_limit=daily_article_limit,
            )
            return _after_action(request, detail, "success", step_id="schedule", action_title="安装计划任务")
        except Exception as exc:
            return _after_action(request, f"安装计划任务失败：{exc}", "error", step_id="schedule", action_title="安装计划任务")

    @web.post("/actions/save-output-dir")
    def save_output_dir(request: Request, briefing_dir: str = Form(...)):
        try:
            detail = request.app.state.backend.save_output_dir(briefing_dir)
            return _after_action(request, detail, "success", step_id="output_dir", action_title="保存 Markdown 目录")
        except Exception as exc:
            return _after_action(request, f"保存 Markdown 目录失败：{exc}", "error", step_id="output_dir", action_title="保存 Markdown 目录")

    @web.post("/actions/pick-output-dir")
    async def pick_output_dir(request: Request):
        try:
            ok, detail = request.app.state.backend.pick_output_dir()
            return _after_action(request, detail, "success" if ok else "info", step_id="output_dir", action_title="选择 Markdown 目录")
        except Exception as exc:
            return _after_action(request, f"打开目录选择器失败：{exc}", "error", step_id="output_dir", action_title="选择 Markdown 目录")

    @web.post("/actions/remove-schedule")
    def remove_schedule_action(request: Request):
        blocked = _block_if_docker_unavailable(request, "删除计划任务", scope="advanced")
        if blocked is not None:
            return blocked
        try:
            detail = request.app.state.backend.remove_schedule()
            return _after_action(request, detail, "success", scope="advanced", action_title="删除计划任务")
        except Exception as exc:
            return _after_action(request, f"删除计划任务失败：{exc}", "error", scope="advanced", action_title="删除计划任务")

    @web.post("/actions/run-now")
    def run_now(request: Request, target_date: str = Form(...)):
        blocked = _block_if_docker_unavailable(request, "立即运行测试", step_id="run_once")
        if blocked is not None:
            return blocked
        try:
            detail_date = date.fromisoformat(target_date)
            ok, detail = request.app.state.backend.run_now(detail_date)
            return _after_action(request, detail, "success" if ok else "warning", step_id="run_once", action_title="立即运行测试")
        except Exception as exc:
            return _after_action(request, f"立即运行失败：{exc}", "error", step_id="run_once", action_title="立即运行测试")

    @web.post("/actions/save-advanced")
    def save_advanced(request: Request, yaml_text: str = Form(...)):
        try:
            detail = request.app.state.backend.save_advanced_yaml(yaml_text)
            return _after_action(request, detail, "success", scope="advanced", action_title="保存高级配置")
        except Exception as exc:
            return _after_action(request, f"保存高级配置失败：{exc}", "error", scope="advanced", action_title="保存高级配置")

    @web.get("/briefings/latest")
    def latest_briefing(request: Request):
        briefings = request.app.state.backend.build_dashboard_context()["briefings"]
        if not briefings:
            return _after_action(request, "还没有生成过 Markdown 日报。", "warning", step_id="briefing", action_title="查看最新日报")
        return RedirectResponse(url=f"/briefings/{briefings[0].date_text}", status_code=303)

    @web.get("/briefings/{briefing_date}", response_class=HTMLResponse)
    def view_briefing(request: Request, briefing_date: str):
        try:
            file_path, content = request.app.state.backend.read_briefing(briefing_date)
        except Exception as exc:
            return _after_action(request, f"打开日报失败：{exc}", "error", step_id="briefing", action_title="打开日报")
        return templates.TemplateResponse(
            request,
            "briefing.html",
            {
                "briefing_date": briefing_date,
                "briefing_path": str(file_path),
                "content": content,
            },
        )

    return web


def _render_error_response(request: Request, *, title: str, description: str, status_code: int = 500) -> HTMLResponse:
    log_path = get_runtime_paths().logs_dir / "gzhreader.log"
    context = {
        "error_title": title,
        "error_description": description,
        "request_path": request.url.path,
        "log_path": str(log_path),
    }
    templates = getattr(request.app.state, "templates", None)
    if templates is not None:
        try:
            return templates.TemplateResponse(request, "error.html", context, status_code=status_code)
        except Exception:
            LOGGER.exception("Failed to render branded error page")

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
    body {{ margin: 0; font-family: "Microsoft YaHei", "Segoe UI", sans-serif; background: linear-gradient(180deg, #eef4ff 0%, #f7faff 100%); color: #10233f; }}
    .shell {{ max-width: 760px; margin: 0 auto; padding: 48px 20px; }}
    .card {{ background: #fff; border: 1px solid #d9e3f2; border-radius: 28px; padding: 28px; box-shadow: 0 22px 48px rgba(15, 23, 42, 0.08); }}
    h1 {{ margin: 0 0 12px; font-size: 30px; }}
    p {{ line-height: 1.8; color: #51657f; }}
    .meta {{ margin-top: 18px; padding: 14px 16px; border-radius: 18px; background: #f8fbff; border: 1px solid #dbeafe; color: #35537a; line-height: 1.8; overflow-wrap: anywhere; }}
    .actions {{ display: flex; gap: 12px; flex-wrap: wrap; margin-top: 22px; }}
    .button {{ display: inline-flex; align-items: center; justify-content: center; min-height: 44px; padding: 0 16px; border-radius: 14px; text-decoration: none; font-weight: 600; }}
    .button.primary {{ background: linear-gradient(135deg, #1d4ed8, #2563eb); color: #fff; }}
    .button.secondary {{ background: #eef4ff; color: #173b70; }}
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


def _after_action(
    request: Request,
    message: str,
    level: str,
    *,
    step_id: str | None = None,
    action_title: str = "最近操作",
    scope: str = "wizard",
):
    if request.headers.get("HX-Request") == "true":
        action_result = _build_action_result(
            scope=scope,
            title=action_title,
            message=message,
            level=level,
            step_id=step_id,
        )
        context = request.app.state.backend.build_dashboard_context(message=message, level=level, action_result=action_result)
        return request.app.state.templates.TemplateResponse(request, "partials/action_updates.html", context)
    return _redirect("/", message, level)


def _redirect(path: str, message: str, level: str) -> RedirectResponse:
    query = urlencode({"message": message, "level": level})
    return RedirectResponse(url=f"{path}?{query}", status_code=303)


def _block_if_docker_unavailable(
    request: Request,
    action_name: str,
    *,
    step_id: str | None = None,
    scope: str = "wizard",
):
    docker_ok, docker_detail = request.app.state.backend.is_docker_ready()
    if docker_ok:
        return None
    return _after_action(
        request,
        f"Docker Desktop 还没准备好，暂时不能{action_name}。{docker_detail}",
        "warning",
        step_id=step_id,
        action_title=action_name,
        scope=scope,
    )


def _classify_docker_problem(detail: str) -> tuple[str, str, str]:
    normalized = detail.lower()
    if "docker 不可用" in detail and ("not found" in normalized or "winerror 2" in normalized or "找不到" in detail):
        return (
            "这台电脑还没有安装 Docker Desktop",
            "GZHReader 需要 Docker Desktop 来启动 wewe-rss-app 和 mysql。先装好它，后面的 RSS 服务才能一键启动。",
            "先点击“下载 Docker Desktop”，安装完成后启动它，再回来点“重新检测”。",
        )
    if "引擎不可用" in detail:
        return (
            "Docker Desktop 已安装，但引擎还没准备好",
            "通常表示 Docker Desktop 还没启动完成，或者 Docker Engine 当前没有运行。",
            "请先打开 Docker Desktop，等它显示 Engine running 后，再回来点“重新检测”。",
        )
    return (
        "Docker 当前不可用",
        "GZHReader 需要 Docker Desktop 来启动 wewe-rss-app 和 mysql。",
        "请先安装并启动 Docker Desktop，再回来点“重新检测”。",
    )


def _display_path(value: str) -> str:
    try:
        return str(Path(value).expanduser().resolve())
    except Exception:
        return value


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


def _build_daily_time(run_hour: int, run_minute: int) -> str:
    if not (0 <= run_hour <= 23):
        raise ValueError("小时必须在 0 到 23 之间")
    if not (0 <= run_minute <= 59):
        raise ValueError("分钟必须在 0 到 59 之间")
    return f"{run_hour:02d}:{run_minute:02d}"
