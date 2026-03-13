import os
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
    def __init__(
        self,
        *,
        docker_ready: bool = True,
        schedule_installed: bool = False,
        llm_save_result: tuple[bool, str] = (True, "saved"),
    ) -> None:
        self.docker_ready = docker_ready
        self.schedule_installed = schedule_installed
        self.llm_save_result = llm_save_result
        self.started = False
        self.saved_schedule_args: dict | None = None
        self.saved_output_dir: str | None = None
        self.pick_output_dir_called = False
        self.open_output_dir_called = False
        self.remove_schedule_called = False
        self.run_now_target: str | None = None

    def build_dashboard_context(
        self,
        message: str = "",
        level: str = "info",
        action_result: dict | None = None,
    ) -> dict:
        config = AppConfig()
        config.source.url = "http://localhost:4000/feeds/all.atom"
        config.wewe_rss.base_url = "http://localhost:4000"
        config.wewe_rss.auth_code = "123567"
        config.output.briefing_dir = "C:/demo/output/briefings"
        config.llm.base_url = "https://example.com/v1"
        config.llm.model = "gpt-4.1-mini"
        config.llm.api_key = "sk-live-secret"

        briefing = BriefingFile(
            name="2026-03-07.md",
            date_text="2026-03-07",
            path="C:/demo/output/briefings/2026-03-07.md",
        )

        docker_setup = {
            "blocked": not self.docker_ready,
            "status_title": "这台电脑还没有安装 Docker Desktop" if not self.docker_ready else "Docker Desktop 已就绪",
            "status_badge": "需先处理" if not self.docker_ready else "已通过",
            "description": (
                "GZHReader 需要 Docker Desktop 来启动 wewe-rss-app 和 mysql。"
                if not self.docker_ready
                else "Docker 已可用，现在可以继续后续向导步骤。"
            ),
            "next_step": "请先安装 Docker Desktop。" if not self.docker_ready else "继续执行第 2 步，启动 RSS 服务。",
            "detail": "Docker 不可用：not found" if not self.docker_ready else "Docker version 28.0",
            "download_url": "https://docs.docker.com/desktop/setup/install/windows-install/",
            "install_url": "https://docs.docker.com/desktop/",
        }

        status = {
            "docker_ok": self.docker_ready,
            "docker_detail": docker_setup["detail"],
            "environment_ready": self.docker_ready,
            "environment": [{"label": "Docker Desktop", "ok": self.docker_ready, "detail": docker_setup["detail"]}],
            "environment_items": [{"label": "Docker Desktop", "ok": self.docker_ready, "detail": docker_setup["detail"]}],
            "rss_service_ready": self.docker_ready,
            "rss_service": [{"label": "wewe-rss-app", "ok": self.docker_ready, "detail": "ok"}],
            "rss_service_items": [{"label": "wewe-rss-app", "ok": self.docker_ready, "detail": "ok"}],
            "source_ok": self.docker_ready,
            "source_detail": "聚合源可用，订阅后会显示在 all.atom 里。" if self.docker_ready else "需要先启动 Docker 和 RSS 服务。",
            "llm_configured": True,
            "llm_detail": "LLM 已配置。",
            "schedule_installed": self.schedule_installed,
            "schedule_detail": "计划任务已安装" if self.schedule_installed else "计划任务未安装",
            "daily_article_limit_label": "每天最多 20 篇",
        }

        raw_steps = [
            {"id": "environment", "number": 1, "title": "检查环境", "summary": "ok", "detail": "ok", "done": self.docker_ready},
            {"id": "rss_service", "number": 2, "title": "启动 RSS 服务", "summary": "ok", "detail": "ok", "done": self.docker_ready},
            {"id": "subscription", "number": 3, "title": "登录并订阅公众号", "summary": "ok", "detail": "ok", "done": self.docker_ready},
            {"id": "llm", "number": 4, "title": "配置并测试 LLM", "summary": "ok", "detail": "ok", "done": self.docker_ready},
            {"id": "output_dir", "number": 5, "title": "选择生成结果保存位置", "summary": "ok", "detail": "当前保存目录：C:/demo/output/briefings", "done": True},
            {"id": "schedule", "number": 6, "title": "设置每日任务", "summary": "ok", "detail": status["schedule_detail"], "done": self.schedule_installed},
            {"id": "run_once", "number": 7, "title": "立即运行一次测试", "summary": "ok", "detail": "最近一次已生成：2026-03-07", "done": True},
            {"id": "briefing", "number": 8, "title": "查看生成结果", "summary": "ok", "detail": briefing.path, "done": True},
        ]
        current_index = next((index for index, step in enumerate(raw_steps) if not step["done"]), len(raw_steps) - 1)
        unlocked = True
        wizard_steps = []
        for index, step in enumerate(raw_steps):
            locked = not unlocked
            current = index == current_index and not locked
            feedback = None
            if action_result and action_result.get("scope") == "wizard" and action_result.get("step_id") == step["id"]:
                feedback = action_result
            wizard_steps.append({**step, "locked": locked, "current": current, "action_feedback": feedback})
            if not step["done"]:
                unlocked = False

        advanced_feedback = action_result if action_result and action_result.get("scope") == "advanced" else None

        return {
            "config": config,
            "config_path": "C:/demo/config.yaml",
            "message": message,
            "level": level,
            "status": status,
            "wizard_steps": wizard_steps,
            "briefings": [briefing],
            "latest_briefing": briefing,
            "today": "2026-03-08",
            "yaml_text": "source:\n  mode: aggregate\n",
            "schedule_hour": 21,
            "schedule_minute": 30,
            "daily_article_limit": "20",
            "daily_article_limit_options": [
                {"value": "all", "label": "当天全部"},
                {"value": "20", "label": "每天最多 20 篇"},
                {"value": "30", "label": "每天最多 30 篇"},
                {"value": "40", "label": "每天最多 40 篇"},
                {"value": "50", "label": "每天最多 50 篇"},
                {"value": "100", "label": "每天最多 100 篇"},
            ],
            "briefing_dir_display": "C:/demo/output/briefings",
            "docker_blocked": not self.docker_ready,
            "docker_setup": docker_setup,
            "llm_api_key_saved": True,
            "llm_api_key_source": "config",
            "llm_uses_env_api_key": False,
            "advanced_feedback": advanced_feedback,
            "terminal_notice": "运行某些步骤时，程序可能会短暂打开终端或系统窗口。这是正常现象，不需要手动关闭。",
        }

    def is_docker_ready(self):
        return self.docker_ready, ("Docker version 28.0" if self.docker_ready else "Docker 不可用：not found")

    def start_rss(self) -> str:
        self.started = True
        return "started"

    def stop_rss(self) -> str:
        return "stopped"

    def open_wewe_rss(self) -> str:
        return "opened"

    def open_output_dir(self) -> str:
        self.open_output_dir_called = True
        return "opened output dir"

    def save_llm(self, **kwargs):
        return self.llm_save_result

    def save_schedule(self, **kwargs):
        self.saved_schedule_args = kwargs
        return "schedule saved"

    def install_schedule(self, **kwargs):
        self.saved_schedule_args = kwargs
        return "schedule installed"

    def save_output_dir(self, briefing_dir: str):
        self.saved_output_dir = briefing_dir
        return f"saved output dir: {briefing_dir}"

    def pick_output_dir(self):
        self.pick_output_dir_called = True
        return True, "picked output dir"

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


def test_dashboard_homepage_renders_docker_setup_when_blocked() -> None:
    client = TestClient(create_app(backend=FakeBackend(docker_ready=False)))

    response = client.get("/")

    assert response.status_code == 200
    assert "下载 Docker Desktop" in response.text
    assert "这台电脑还没有安装 Docker Desktop" in response.text
    assert "分步向导" not in response.text


def test_dashboard_homepage_renders_linear_wizard_when_docker_ready() -> None:
    client = TestClient(create_app(backend=FakeBackend(docker_ready=True)))

    response = client.get("/")

    assert response.status_code == 200
    assert "分步向导" in response.text
    assert "检查环境" in response.text
    assert "启动 RSS 服务" in response.text
    assert "查看生成结果" in response.text


def test_homepage_renders_wewe_rss_app_with_code_tag() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/")

    assert response.status_code == 200
    assert "<code>wewe-rss-app</code>" in response.text
    assert "`wewe-rss-app`" not in response.text


def test_subscription_step_shows_auth_code_and_rss_manual() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/")

    assert response.status_code == 200
    assert "AUTH_CODE" in response.text
    assert "123567" in response.text
    assert "账号管理" in response.text
    assert "更新全部" in response.text
    assert "刷新状态" in response.text


def test_sidebar_cards_explain_key_links() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/")

    assert response.status_code == 200
    assert "聚合 RSS / all.atom" in response.text
    assert "这是 GZHReader 真正读取的聚合源，不是给你登录的页面" in response.text
    assert "wewe-rss 后台" in response.text
    assert "这里用来输入" in response.text
    assert "Markdown 输出目录" in response.text
    assert "日报结果保存位置" in response.text


def test_homepage_has_quick_run_today_entry() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/")

    assert response.status_code == 200
    assert "立即抓取今天" in response.text
    assert 'name="action_title" value="立即抓取今天"' in response.text
    assert 'name="target_date" value="2026-03-08"' in response.text


def test_dashboard_renders_sidebar_resizer_and_scroll_anchors() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/")

    assert response.status_code == 200
    assert 'id="sidebar-resizer"' in response.text
    assert 'role="separator"' in response.text
    assert 'id="wizard-step-environment"' in response.text
    assert 'id="wizard-step-rss_service"' in response.text
    assert 'id="wizard-step-llm"' in response.text
    assert 'id="wizard-step-schedule"' in response.text


def test_sidebar_places_overview_before_key_entries_and_has_scroll_cards() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/")

    assert response.status_code == 200
    assert response.text.index("运行概况") < response.text.index("关键入口")
    assert 'data-scroll-target="#wizard-step-environment"' in response.text
    assert 'data-scroll-target="#wizard-step-rss_service"' in response.text
    assert 'data-scroll-target="#wizard-step-llm"' in response.text
    assert 'data-scroll-target="#wizard-step-schedule"' in response.text


def test_sidebar_key_entries_render_as_collapsed_details() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/")

    assert response.status_code == 200
    assert '<details class="sidebar-entry-card" data-sidebar-section="aggregate-feed">' in response.text
    assert '<details class="sidebar-entry-card" data-sidebar-section="wewe-admin">' in response.text
    assert '<details class="sidebar-entry-card" data-sidebar-section="briefing-directory">' in response.text
    assert 'gzhreader.sidebar.width' in response.text
    assert 'gzhreader.sidebar.sections' in response.text


def test_hero_renders_primary_setup_cta_and_schedule_jump() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/")

    assert response.status_code == 200
    assert "<h1>GZHReader</h1>" in response.text
    assert 'data-scroll-target="#wizard-shell"' in response.text
    assert "开始设置" in response.text
    assert "聚合 RSS → 正文补抓 → LLM 总结" in response.text
    assert "默认只保留 Markdown 日报" in response.text
    assert "支持立即运行与每日自动运行" in response.text
    assert 'data-scroll-target="#wizard-step-schedule"' in response.text
    assert "去设置" in response.text


def test_schedule_step_shows_stateful_actions_when_not_installed() -> None:
    client = TestClient(create_app(backend=FakeBackend(schedule_installed=False)))

    response = client.get("/")

    assert response.status_code == 200
    assert "保存并开启定时任务" in response.text
    assert "关闭定时任务" not in response.text


def test_schedule_step_shows_stateful_actions_when_installed() -> None:
    client = TestClient(create_app(backend=FakeBackend(schedule_installed=True)))

    response = client.get("/")

    assert response.status_code == 200
    assert "修改计划任务" in response.text
    assert "关闭定时任务" in response.text


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


def test_start_rss_action_returns_htmx_fragment() -> None:
    backend = FakeBackend()
    client = TestClient(create_app(backend=backend))

    response = client.post("/actions/start-rss", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert backend.started is True
    assert "hx-swap-oob" in response.text
    assert "flash-area" in response.text
    assert "sidebar-shell" in response.text
    assert "top-chrome" in response.text
    assert "main-shell" in response.text


def test_start_rss_is_blocked_when_docker_unavailable() -> None:
    backend = FakeBackend(docker_ready=False)
    client = TestClient(create_app(backend=backend))

    response = client.post("/actions/start-rss", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert backend.started is False
    assert "Docker Desktop 还没准备好" in response.text
    assert "下载 Docker Desktop" in response.text


def test_start_rss_action_redirects_without_htmx() -> None:
    backend = FakeBackend()
    client = TestClient(create_app(backend=backend))

    response = client.post("/actions/start-rss", follow_redirects=False)

    assert response.status_code == 303
    assert backend.started is True
    assert response.headers["location"].startswith("/?message=")


def test_schedule_form_uses_hour_minute_and_daily_limit_inputs() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/")

    assert response.status_code == 200
    assert 'name="run_hour"' in response.text
    assert 'name="run_minute"' in response.text
    assert 'name="daily_article_limit"' in response.text
    assert 'id="schedule-hour-select"' in response.text
    assert 'id="schedule-minute-select"' in response.text
    assert 'id="daily-article-limit-select"' in response.text
    assert 'id="schedule-preview-time"' in response.text
    assert 'id="schedule-preview-limit"' in response.text
    assert "按本机时间自动执行" in response.text
    assert "每天最多处理多少篇" in response.text
    assert "当天全部" in response.text


def test_results_section_contains_output_dir_controls() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/")

    assert response.status_code == 200
    assert 'name="briefing_dir"' in response.text
    assert "选择目录" in response.text
    assert "打开目录" in response.text
    assert "Markdown 保存目录" in response.text


def test_install_schedule_passes_hour_minute_and_daily_limit_to_backend() -> None:
    backend = FakeBackend()
    client = TestClient(create_app(backend=backend))

    response = client.post(
        "/actions/install-schedule",
        data={
            "run_hour": "21",
            "run_minute": "30",
            "daily_article_limit": "40",
            "action_title": "修改计划任务",
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert backend.saved_schedule_args == {"run_hour": 21, "run_minute": 30, "daily_article_limit": "40"}
    assert "修改计划任务" in response.text


def test_save_output_dir_passes_directory_to_backend() -> None:
    backend = FakeBackend()
    client = TestClient(create_app(backend=backend))

    response = client.post(
        "/actions/save-output-dir",
        data={"briefing_dir": "D:/Briefings"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert backend.saved_output_dir == "D:/Briefings"


def test_open_output_dir_calls_backend() -> None:
    backend = FakeBackend()
    client = TestClient(create_app(backend=backend))

    response = client.post("/actions/open-output-dir", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert backend.open_output_dir_called is True
    assert "opened output dir" in response.text


def test_pick_output_dir_calls_backend() -> None:
    backend = FakeBackend()
    client = TestClient(create_app(backend=backend))

    response = client.post("/actions/pick-output-dir", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert backend.pick_output_dir_called is True


def test_remove_schedule_can_be_triggered_from_wizard_without_docker() -> None:
    backend = FakeBackend(docker_ready=False, schedule_installed=True)
    client = TestClient(create_app(backend=backend))

    response = client.post(
        "/actions/remove-schedule",
        data={"scope": "wizard", "step_id": "schedule", "action_title": "关闭定时任务"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert backend.remove_schedule_called is True
    assert "schedule removed" in response.text
    assert "Docker Desktop 还没准备好" not in response.text


def test_run_now_quick_action_posts_today() -> None:
    backend = FakeBackend()
    client = TestClient(create_app(backend=backend))

    response = client.post(
        "/actions/run-now",
        data={"target_date": "2026-03-08", "step_id": "run_once", "action_title": "立即抓取今天"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert backend.run_now_target == "2026-03-08"
    assert "run on 2026-03-08" in response.text
    assert "立即抓取今天" in response.text


def test_docker_setup_partial_contains_official_links_and_wsl_guidance() -> None:
    client = TestClient(create_app(backend=FakeBackend(docker_ready=False)))

    response = client.get("/partials/docker-setup")

    assert response.status_code == 200
    assert 'href="https://docs.docker.com/desktop/setup/install/windows-install/"' in response.text
    assert 'href="https://docs.docker.com/desktop/"' in response.text
    assert 'href="https://learn.microsoft.com/windows/wsl/install"' in response.text
    assert 'href="https://docs.docker.com/desktop/features/wsl/"' in response.text
    assert "WSL2" in response.text
    assert "Virtual Machine Platform" in response.text
    assert "Linux 发行版" in response.text
    assert "Engine running" in response.text


def test_dashboard_mentions_terminal_window_notice() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/")

    assert response.status_code == 200
    assert "程序可能会短暂打开终端或系统窗口" in response.text


def test_save_llm_failure_renders_step_feedback_near_llm_module() -> None:
    backend = FakeBackend(llm_save_result=(False, "LLM 配置已保存，但测试失败：network timeout"))
    client = TestClient(create_app(backend=backend))

    response = client.post(
        "/actions/save-llm",
        data={
            "base_url": "https://example.com/v1",
            "api_key": "test-key",
            "model": "gpt-4.1-mini",
            "timeout_seconds": "30",
            "retries": "2",
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "LLM 配置已保存，但测试失败：network timeout" in response.text
    assert 'data-step-feedback="llm"' in response.text


def test_save_advanced_renders_visible_local_feedback() -> None:
    backend = FakeBackend()
    client = TestClient(create_app(backend=backend))

    response = client.post(
        "/actions/save-advanced",
        data={"yaml_text": "source:\n  mode: aggregate\n"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert 'data-step-feedback="advanced"' in response.text
    assert "<details open>" in response.text


def test_briefing_page_renders_markdown_text() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/briefings/2026-03-07")

    assert response.status_code == 200
    assert "测试日报" in response.text


def test_embedded_htmx_asset_is_served() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/assets/htmx.min.js")

    assert response.status_code == 200
    assert "htmx" in response.text


def test_dashboard_hides_saved_api_key_and_renders_password_toggle() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/")

    assert response.status_code == 200
    assert "sk-live-secret" not in response.text
    assert 'type="password"' in response.text
    assert 'data-toggle-password' in response.text


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
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(latest, (1_800_000_000, 1_800_000_000))
    os.utime(ignored, (1_900_000_000, 1_900_000_000))

    config_path = tmp_path / "config.yaml"
    config = AppConfig(output=OutputConfig(briefing_dir=str(output_dir)))
    save_config(config, config_path)
    backend = DashboardBackend(config_path)

    briefings = backend.list_briefings(config)

    assert [item.date_text for item in briefings] == ["2026-03-08", "2026-03-07"]
