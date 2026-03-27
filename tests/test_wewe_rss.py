from pathlib import Path

from gzhreader.config import RSSServiceConfig
from gzhreader.rss_service import BundledRSSServiceManager
from gzhreader.runtime_paths import RuntimePaths


def _runtime_paths(tmp_path: Path) -> RuntimePaths:
    return RuntimePaths(
        state_dir=tmp_path / "state",
        config_path=tmp_path / "state" / "config.yaml",
        data_dir=tmp_path / "state" / "data",
        db_path=tmp_path / "state" / "data" / "gzhreader.db",
        rss_service_dir=tmp_path / "state" / "wewe-rss",
        rss_service_data_dir=tmp_path / "state" / "wewe-rss" / "data",
        rss_service_db_path=tmp_path / "state" / "wewe-rss" / "data" / "wewe-rss.db",
        rss_service_pid_file=tmp_path / "state" / "wewe-rss" / "wewe-rss.pid",
        rss_service_log_path=tmp_path / "state" / "logs" / "wewe-rss.log",
        output_dir=tmp_path / "Documents" / "GZHReader",
        raw_archive_dir=tmp_path / "state" / "output" / "raw",
        logs_dir=tmp_path / "state" / "logs",
        resource_dir=tmp_path,
        bundled_wewe_rss_source_dir=tmp_path / "third_party" / "wewe-rss",
        bundled_wewe_rss_runtime_dir=tmp_path / "bundle" / "wewe-rss-runtime",
    )


def test_bundled_rss_manager_reports_missing_runtime(tmp_path) -> None:
    manager = BundledRSSServiceManager(
        RSSServiceConfig(data_dir=str(tmp_path / "data"), log_file=str(tmp_path / "logs" / "wewe-rss.log")),
        runtime_paths=_runtime_paths(tmp_path),
    )

    ok, detail = manager.check_runtime()

    assert ok is False
    assert "build_wewe_rss.ps1" in detail


def test_bundled_rss_manager_reads_log_tail(tmp_path) -> None:
    runtime_paths = _runtime_paths(tmp_path)
    runtime_paths.logs_dir.mkdir(parents=True, exist_ok=True)
    runtime_paths.rss_service_log_path.write_text("line1\nline2\nline3\n", encoding="utf-8")
    manager = BundledRSSServiceManager(
        RSSServiceConfig(data_dir=str(tmp_path / "data"), log_file=str(runtime_paths.rss_service_log_path)),
        runtime_paths=runtime_paths,
    )

    output = manager.logs(tail=2)

    assert output == "line2\nline3"


def test_bundled_rss_manager_disables_auth_code_in_runtime_env(tmp_path) -> None:
    runtime_paths = _runtime_paths(tmp_path)
    manager = BundledRSSServiceManager(
        RSSServiceConfig(
            auth_code="123567",
            data_dir=str(tmp_path / "data"),
            log_file=str(runtime_paths.rss_service_log_path),
        ),
        runtime_paths=runtime_paths,
    )

    env = manager._build_runtime_env()

    assert env["AUTH_CODE"] == ""
