from pathlib import Path

from fastapi.testclient import TestClient

from gzhreader.config import AppConfig, OutputConfig, save_config
import gzhreader.webapp as webapp
from gzhreader.webapp import (
    LLM_API_KEY_PLACEHOLDER,
    BriefingFile,
    DashboardBackend,
    _build_llm_status,
    _build_redacted_yaml,
    create_app,
)


class FakeBackend:
    def __init__(self, *, service_ready: bool = True, schedule_installed: bool = False) -> None:
        self.service_ready = service_ready
        self.schedule_installed = schedule_installed
        self.started = False
        self.stopped = False
        self.restarted = False
        self.opened_admin = False
        self.admin_return_to: str | None = None
        self.saved_service: dict | None = None
        self.saved_llm: dict | None = None
        self.pick_output_dir_called = False
        self.open_output_dir_called = False
        self.saved_schedule_args: dict | None = None
        self.remove_schedule_called = False
        self.run_now_target: str | None = None

    def _config(self) -> AppConfig:
        config = AppConfig()
        config.source.url = "http://127.0.0.1:4000/feeds/all.atom"
        config.rss_service.base_url = "http://127.0.0.1:4000"
        config.rss_service.auth_code = ""
        config.output.briefing_dir = "C:/demo/output/briefings"
        config.llm.base_url = "https://example.com/v1"
        config.llm.model = "gpt-4.1-mini"
        config.llm.api_key = "sk-live-secret"
        return config

    def build_home_context(self, message: str = "", level: str = "info") -> dict:
        briefing = BriefingFile(
            name="2026-03-07.md",
            date_text="2026-03-07",
            path="C:/demo/output/briefings/2026-03-07.md",
        )
        status = {
            "service": {
                "runtime_ok": self.service_ready,
                "runtime_detail": "bundled runtime ready" if self.service_ready else "runtime missing",
                "process_ok": self.service_ready,
                "process_detail": "service process ready" if self.service_ready else "service not running",
                "web_ok": self.service_ready,
                "web_detail": "service reachable" if self.service_ready else "service unreachable",
                "admin_url": "http://127.0.0.1:4000/dash",
                "feed_url": "http://127.0.0.1:4000/feeds/all.atom",
            },
            "llm": {
                "configured": True,
                "detail": "AI 模型配置已经保存，需要摘要时会直接继续使用。",
                "api_key_source": "config",
                "api_key_saved": True,
                "uses_env_api_key": False,
            },
            "schedule": {
                "installed": self.schedule_installed,
                "detail": "已经开启每日自动整理" if self.schedule_installed else "还没有开启每日自动整理",
                "daily_limit_label": "每天最多 20 篇",
            },
            "source": {
                "ok": self.service_ready,
                "detail": "聚合源可读取" if self.service_ready else "聚合源暂不可用",
            },
        }
        return {
            "page_title": "工作台",
            "message": message,
            "level": level,
            "config": self._config(),
            "status": status,
            "home_summary": {
                "headline": "今日日报已经成刊" if self.service_ready else "今天的日报还在整理中",
                "detail": "最近一次日报：2026-03-07",
                "status_label": "已成刊" if self.service_ready else "整理中",
            },
            "quick_actions": [],
            "recent_briefings": [briefing],
            "latest_briefing": briefing,
            "today": "2026-03-08",
            "settings_snapshot": {
                "llm": status["llm"]["detail"],
                "schedule": status["schedule"]["detail"],
                "output_dir": "C:/demo/output/briefings",
            },
            "theme_state": {
                "default": "system",
                "options": [
                    {"value": "system", "label": "跟随系统"},
                    {"value": "light", "label": "浅色"},
                    {"value": "dark", "label": "深色"},
                ],
            },
            "about_modal": {
                "button_label": "关于",
                "dialog_id": "about-dialog",
                "tagline": "把公众号阅读整理成更安静的本地工作台",
                "motivation_title": "💡 开发动机",
                "motivation_text": "从公众号碎片化的推送中解放出来，比起被小红点牵着走，更希望把优质内容安静地收进本地、沉淀为日报，留给真正需要深度阅读的时刻",
                "repo_url": "https://github.com/zhiwuyazhe-fjr/GZHReader",
                "feedback_title": "💬 问题反馈",
                "feedback_text": "遇到账号配置、内容抓取、日报生成或交互体验上的问题，欢迎随时提 Issue！特别是那些让你觉得“多点了一步”、“等得太久”或“提示看不懂”的细节，都是接下来的优化方向",
                "issues_url": "https://github.com/zhiwuyazhe-fjr/GZHReader/issues",
                "feedback_label": "反馈问题",
                "support_title": "❤️ 支持项目",
                "support_text": "如果 GZHReader 帮你减少了信息噪音，让阅读整理更省心，请把它分享给有同样困扰的朋友。你的每一次安利，都是我持续把这个工具做稳、做精的最大底气😘",
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
                "footer_lines": ["本地优先", "阅读整理工作台"],
            },
            "app_version": "2.0.0",
            "app_version_display": "2.0.0",
        }

    def build_settings_context(self, message: str = "", level: str = "info") -> dict:
        config = self._config()
        status = self.build_home_context()["status"]
        return {
            "page_title": "设置",
            "message": message,
            "level": level,
            "config": config,
            "status": status,
            "yaml_text": "source:\n  mode: aggregate\n",
            "schedule_hour": 21,
            "schedule_minute": 30,
            "daily_article_limit": "20",
            "daily_article_limit_options": [
                {"value": "all", "label": "当天全部"},
                {"value": "20", "label": "每天最多 20 篇"},
            ],
            "briefing_dir_display": "C:/demo/output/briefings",
            "llm_api_key_saved": True,
            "llm_api_key_source": "config",
            "llm_uses_env_api_key": False,
            "theme_state": {
                "default": "system",
                "options": [
                    {"value": "system", "label": "跟随系统"},
                    {"value": "light", "label": "浅色"},
                    {"value": "dark", "label": "深色"},
                ],
            },
            "about_modal": self.build_home_context()["about_modal"],
            "app_version": "2.0.0",
            "app_version_display": "2.0.0",
        }

    def start_service(self) -> str:
        self.started = True
        return "started"

    def stop_service(self) -> str:
        self.stopped = True
        return "stopped"

    def restart_service(self) -> str:
        self.restarted = True
        return "restarted"

    def open_service_admin(self, *, return_to: str = "") -> str:
        self.opened_admin = True
        self.admin_return_to = return_to
        return "opened admin"

    def save_service_settings(self, **kwargs) -> str:
        self.saved_service = kwargs
        return "service settings saved"

    def save_llm(self, **kwargs):
        self.saved_llm = kwargs
        return True, "llm saved"

    def pick_output_dir(self):
        self.pick_output_dir_called = True
        return True, "picked output dir"

    def open_output_dir(self) -> str:
        self.open_output_dir_called = True
        return "opened output dir"

    def install_schedule(self, **kwargs):
        self.saved_schedule_args = kwargs
        return "schedule installed"

    def remove_schedule(self):
        self.remove_schedule_called = True
        return "schedule removed"

    def run_now(self, target_date):
        self.run_now_target = target_date.isoformat()
        return True, f"run on {target_date.isoformat()}"

    def save_advanced_yaml(self, yaml_text: str):
        return yaml_text

    def read_briefing(self, briefing_date: str):
        return Path(f"C:/demo/output/briefings/{briefing_date}.md"), "# 测试日报"


def test_homepage_renders_workspace_instead_of_docker_setup() -> None:
    client = TestClient(create_app(backend=FakeBackend(service_ready=False)))

    response = client.get("/")

    assert response.status_code == 200
    assert "工作台" in response.text
    assert "立即生成今天" in response.text
    assert "打开公众号后台" in response.text
    assert "进入设置" in response.text
    assert "今天的日报还在整理中" in response.text
    assert "今天还没有生成日报" not in response.text
    assert "Docker Desktop" not in response.text
    assert "分步向导" not in response.text


def test_settings_page_renders_editorial_sections() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/settings")

    assert response.status_code == 200
    assert "公众号服务" in response.text
    assert "AI模型配置" in response.text
    assert "输出与归档" in response.text
    assert "自动运行" in response.text
    assert "高级配置" in response.text
    assert "把服务与设置留在幕后" in response.text
    assert "没有配置AI也可以生成纯整理版日报" in response.text
    assert "启用自动运行" in response.text
    assert "保存输出目录" not in response.text
    assert "安装自动运行" not in response.text
    assert "AUTH_CODE" not in response.text
    assert "跟随系统" in response.text
    assert "浅色" in response.text
    assert "深色" in response.text
    assert 'data-open-admin-form' in response.text
    assert 'class="settings-disclosure"' in response.text
    assert 'data-theme-toggle' not in response.text


def test_service_start_redirects_and_calls_backend() -> None:
    backend = FakeBackend()
    client = TestClient(create_app(backend=backend))

    response = client.post("/actions/service/start", follow_redirects=False)

    assert response.status_code == 303
    assert backend.started is True
    assert response.headers["location"].startswith("/?message=")


def test_open_admin_passes_return_to_to_backend() -> None:
    backend = FakeBackend()
    client = TestClient(create_app(backend=backend))

    response = client.post(
        "/actions/service/open-admin",
        data={"return_to": "http://127.0.0.1:8765/settings"},
        follow_redirects=False,
        headers={"referer": "http://127.0.0.1:8765/settings"},
    )

    assert response.status_code == 303
    assert backend.opened_admin is True
    assert backend.admin_return_to == "http://127.0.0.1:8765/settings"


def test_save_service_settings_passes_values_to_backend() -> None:
    backend = FakeBackend()
    client = TestClient(create_app(backend=backend))

    response = client.post(
        "/actions/service/save",
        data={"port": "4100"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert backend.saved_service == {"port": 4100}


def test_run_now_quick_action_posts_today() -> None:
    backend = FakeBackend()
    client = TestClient(create_app(backend=backend))

    response = client.post(
        "/actions/run-now",
        data={"target_date": "2026-03-08"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert backend.run_now_target == "2026-03-08"


def test_status_api_returns_backend_status() -> None:
    client = TestClient(create_app(backend=FakeBackend(service_ready=True, schedule_installed=True)))

    response = client.get("/api/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["service"]["web_ok"] is True
    assert payload["schedule"]["installed"] is True


def test_run_job_api_creates_background_job() -> None:
    backend = FakeBackend()
    client = TestClient(create_app(backend=backend))

    create_response = client.post("/api/run-jobs", data={"target_date": "2026-03-08"})

    assert create_response.status_code == 200
    job_id = create_response.json()["job_id"]

    status_response = client.get(f"/api/run-jobs/{job_id}")

    assert status_response.status_code == 200
    assert status_response.json()["ok"] is True


def test_run_job_status_uses_human_readable_stage_copy() -> None:
    class ProgressBackend(FakeBackend):
        def run_now(self, target_date, progress_callback=None):
            self.run_now_target = target_date.isoformat()
            if progress_callback is not None:
                progress_callback("正在检查账号", "正在核对账号状态和可用额度")
                progress_callback("正在刷新订阅", "正在更新今天需要整理的订阅内容")
                progress_callback("正在整理日报", "正在把刷新后的内容整理成今天的日报")
            return True, "日报已经生成"

    client = TestClient(create_app(backend=ProgressBackend()))

    create_response = client.post("/api/run-jobs", data={"target_date": "2026-03-08"})
    assert create_response.status_code == 200
    job_id = create_response.json()["job_id"]

    status_response = client.get(f"/api/run-jobs/{job_id}")
    assert status_response.status_code == 200
    payload = status_response.json()
    assert payload["ok"] is True
    assert payload["stage"] != "checking"
    assert payload["stage"] in {"正在检查账号", "正在刷新订阅", "正在整理日报", "已经完成"}


def test_briefing_page_renders_markdown_text() -> None:
    class MarkdownBackend(FakeBackend):
        def read_briefing(self, briefing_date: str):
            return Path(f"C:/demo/output/briefings/{briefing_date}.md"), "\ufeff# 测试日报\n\n- 第一条"

    client = TestClient(create_app(backend=MarkdownBackend()))

    response = client.get("/briefings/2026-03-07")

    assert response.status_code == 200
    assert "测试日报" in response.text
    assert "<h1" in response.text
    assert "<li>第一条</li>" in response.text


def test_healthz_returns_ok() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_favicon_returns_svg_icon() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert "<svg" in response.text


def test_llm_status_uses_env_api_key_when_config_is_empty(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret")
    config = AppConfig()
    config.llm.api_key = ""

    status = _build_llm_status(config)

    assert status["configured"] is True
    assert status["api_key_source"] == "env"
    assert status["uses_env_api_key"] is True


def test_save_llm_keeps_existing_api_key_when_blank(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.yaml"
    config = AppConfig()
    config.llm.api_key = "existing-secret"
    save_config(config, config_path)
    backend = DashboardBackend(config_path)

    monkeypatch.setattr(webapp.OpenAICompatibleSummarizer, "check_connectivity", lambda self: (True, "ok"))

    ok, _detail = backend.save_llm(
        base_url="https://api.openai.com/v1",
        api_key="",
        model="gpt-4o-mini",
        timeout_seconds=45,
        retries=2,
    )

    assert ok is True
    assert backend.load_config().llm.api_key == "existing-secret"


def test_advanced_yaml_redacts_and_preserves_existing_api_key(tmp_path) -> None:
    config_path = tmp_path / "config.yaml"
    config = AppConfig()
    config.llm.api_key = "existing-secret"
    save_config(config, config_path)
    backend = DashboardBackend(config_path)

    yaml_text = _build_redacted_yaml(config)
    backend.save_advanced_yaml(yaml_text)

    assert "existing-secret" not in yaml_text
    assert LLM_API_KEY_PLACEHOLDER in yaml_text
    assert "auth_code" not in yaml_text
    assert backend.load_config().llm.api_key == "existing-secret"


def test_briefings_are_sorted_by_mtime_and_filtered_to_generated_files(tmp_path) -> None:
    output_dir = tmp_path / "briefings"
    output_dir.mkdir()
    older = output_dir / "2026-03-07.md"
    latest = output_dir / "2026-03-08.md"
    ignored = output_dir / "notes.md"
    older.write_text("old", encoding="utf-8")
    latest.write_text("new", encoding="utf-8")
    ignored.write_text("ignore", encoding="utf-8")
    older.touch()
    latest.touch()

    config_path = tmp_path / "config.yaml"
    config = AppConfig(output=OutputConfig(briefing_dir=str(output_dir)))
    save_config(config, config_path)
    backend = DashboardBackend(config_path)

    briefings = backend.list_briefings(config)

    assert [item.date_text for item in briefings] == ["2026-03-08", "2026-03-07"]


def test_homepage_renders_about_trigger_and_dialog() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/")

    assert response.status_code == 200
    assert "about-dialog" in response.text
    assert "data-open-dialog" in response.text
    assert "关于" in response.text
    assert "💡 开发动机" in response.text
    assert "💬 问题反馈" in response.text
    assert "❤️ 支持项目" in response.text
    assert "反馈问题" in response.text
    assert "分享给朋友" in response.text
    assert "仓库链接已复制" in response.text
    assert "GitHub主页" in response.text
    assert "小红书" in response.text
    assert "生成特定日期" in response.text
    assert "v2.0.0" in response.text
    assert "vv2.0.0" not in response.text
    assert 'data-open-admin-form' in response.text
