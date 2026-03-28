from pathlib import Path

from gzhreader.config import RSSServiceConfig
import gzhreader.rss_service as rss_service_module
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


def test_bundled_rss_manager_prefers_sqlite_migration_directory(tmp_path) -> None:
    runtime_paths = _runtime_paths(tmp_path)
    sqlite_migrations = runtime_paths.bundled_wewe_rss_runtime_dir / "apps" / "server" / "prisma" / "migrations"
    sqlite_migrations.mkdir(parents=True, exist_ok=True)

    manager = BundledRSSServiceManager(
        RSSServiceConfig(
            data_dir=str(tmp_path / "data"),
            log_file=str(runtime_paths.rss_service_log_path),
        ),
        runtime_paths=runtime_paths,
    )

    resolved = manager._resolve_sqlite_migrations_root(runtime_paths.bundled_wewe_rss_runtime_dir)

    assert resolved == sqlite_migrations


def test_bundled_rss_manager_prefers_source_runtime_in_dev(monkeypatch, tmp_path) -> None:
    runtime_paths = _runtime_paths(tmp_path)
    source_entry = runtime_paths.bundled_wewe_rss_source_dir / "apps" / "server" / "dist"
    source_entry.mkdir(parents=True, exist_ok=True)
    (source_entry / "main.js").write_text("console.log('ok')", encoding="utf-8")
    packaged_entry = runtime_paths.bundled_wewe_rss_runtime_dir / "apps" / "server" / "dist"
    packaged_entry.mkdir(parents=True, exist_ok=True)
    (packaged_entry / "main.js").write_text("console.log('ok')", encoding="utf-8")

    monkeypatch.setattr(rss_service_module, "is_frozen_app", lambda: False)
    manager = BundledRSSServiceManager(
        RSSServiceConfig(
            data_dir=str(tmp_path / "data"),
            log_file=str(runtime_paths.rss_service_log_path),
        ),
        runtime_paths=runtime_paths,
    )

    assert manager._resolve_runtime_root() == runtime_paths.bundled_wewe_rss_source_dir
