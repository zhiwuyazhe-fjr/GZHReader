import os
from pathlib import Path

from fastapi.testclient import TestClient

from gzhreader.config import AppConfig, OutputConfig, save_config
from gzhreader.webapp import BriefingFile, DashboardBackend, create_app


class FakeBackend:
    def __init__(self, *, docker_ready: bool = True) -> None:
        self.docker_ready = docker_ready
        self.started = False
        self.saved_schedule_args: dict | None = None
        self.saved_output_dir: str | None = None
        self.pick_output_dir_called = False

    def build_dashboard_context(self, message: str = "", level: str = "info") -> dict:
        briefing = BriefingFile(
            name="2026-03-07.md",
            date_text="2026-03-07",
            path="C:/demo/output/briefings/2026-03-07.md",
        )
        docker_setup = {
            "blocked": not self.docker_ready,
            "status_title": "这台电脑还没有安装 Docker Desktop",
            "status_badge": "需先处理",
            "description": "GZHReader 需要 Docker Desktop 来启动 wewe-rss-app 和 mysql。",
            "next_step": "请先安装 Docker Desktop。",
            "detail": "Docker 不可用：not found" if not self.docker_ready else "Docker version 28.0",
            "download_url": "https://www.docker.com/products/docker-desktop/",
            "install_url": "https://docs.docker.com/desktop/setup/install/windows-install/",
        }
        return {
            "config": AppConfig(),
            "config_path": "C:/demo/config.yaml",
            "message": message,
            "level": level,
            "status": {
                "docker_ok": self.docker_ready,
                "docker_detail": docker_setup["detail"],
                "environment_ready": self.docker_ready,
                "environment_items": [{"label": "Docker Desktop", "ok": self.docker_ready, "detail": docker_setup["detail"]}],
                "rss_service_ready": self.docker_ready,
                "rss_service_items": [{"label": "wewe-rss-app", "ok": self.docker_ready, "detail": "ok"}],
                "source_ok": self.docker_ready,
                "source_detail": "ok",
                "llm_configured": True,
                "llm_detail": "ok",
                "schedule_installed": False,
                "schedule_detail": "未安装",
                "daily_article_limit_label": "每天最多 20 篇",
            },
            "wizard_steps": [
                {"id": "environment", "number": 1, "title": "检查环境", "summary": "ok", "detail": "ok", "done": self.docker_ready, "locked": False, "current": not self.docker_ready},
                {"id": "rss_service", "number": 2, "title": "启动 RSS 服务", "summary": "ok", "detail": "ok", "done": self.docker_ready, "locked": not self.docker_ready, "current": False},
                {"id": "subscription", "number": 3, "title": "登录并订阅公众号", "summary": "ok", "detail": "ok", "done": self.docker_ready, "locked": not self.docker_ready, "current": False},
                {"id": "llm", "number": 4, "title": "配置并测试 LLM", "summary": "ok", "detail": "ok", "done": self.docker_ready, "locked": not self.docker_ready, "current": False},
                {"id": "output_dir", "number": 5, "title": "选择生成结果保存位置", "summary": "ok", "detail": "当前保存目录：C:/demo/output/briefings", "done": True, "locked": not self.docker_ready, "current": False},
                {"id": "schedule", "number": 6, "title": "设置每日任务", "summary": "ok", "detail": "ok", "done": False, "locked": not self.docker_ready, "current": self.docker_ready},
                {"id": "run_once", "number": 7, "title": "立即运行一次测试", "summary": "ok", "detail": "ok", "done": True, "locked": False, "current": False},
                {"id": "briefing", "number": 8, "title": "查看生成结果", "summary": "ok", "detail": "ok", "done": True, "locked": False, "current": False},
            ],
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

    def save_llm(self, **kwargs):
        return True, "saved"

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
        return "schedule removed"

    def run_now(self, target_date):
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


def test_healthz_returns_ok() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_favicon_returns_204() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/favicon.ico")

    assert response.status_code == 204


def test_start_rss_action_returns_htmx_fragment() -> None:
    backend = FakeBackend()
    client = TestClient(create_app(backend=backend))

    response = client.post("/actions/start-rss", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert backend.started is True
    assert "hx-swap-oob" in response.text
    assert "flash-area" in response.text
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
    assert 'name="temperature"' not in response.text
    assert 'name="timezone_name"' not in response.text
    assert "按本机时间自动执行" in response.text
    assert "每天最多处理多少篇" in response.text
    assert "当天全部" in response.text
    assert "每天最多 20 篇" in response.text


def test_results_section_contains_output_dir_controls() -> None:
    client = TestClient(create_app(backend=FakeBackend()))

    response = client.get("/")

    assert response.status_code == 200
    assert 'name="briefing_dir"' in response.text
    assert "选择目录" in response.text
    assert "Markdown 保存目录" in response.text


def test_install_schedule_passes_hour_minute_and_daily_limit_to_backend() -> None:
    backend = FakeBackend()
    client = TestClient(create_app(backend=backend))

    response = client.post(
        "/actions/install-schedule",
        data={"run_hour": "21", "run_minute": "30", "daily_article_limit": "40"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert backend.saved_schedule_args == {"run_hour": 21, "run_minute": 30, "daily_article_limit": "40"}


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


def test_pick_output_dir_calls_backend() -> None:
    backend = FakeBackend()
    client = TestClient(create_app(backend=backend))

    response = client.post("/actions/pick-output-dir", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert backend.pick_output_dir_called is True


def test_docker_setup_partial_contains_official_links() -> None:
    client = TestClient(create_app(backend=FakeBackend(docker_ready=False)))

    response = client.get("/partials/docker-setup")

    assert response.status_code == 200
    assert "docker.com/products/docker-desktop" in response.text
    assert "docs.docker.com/desktop/setup/install/windows-install" in response.text


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
