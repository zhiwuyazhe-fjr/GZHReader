from gzhreader import config as config_module
from gzhreader.config import default_config, ensure_config
from gzhreader.runtime_paths import RuntimePaths, build_schedule_command
import gzhreader.runtime_paths as runtime_paths_module


def _runtime_paths(tmp_path):
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


def test_default_config_uses_runtime_paths(monkeypatch, tmp_path) -> None:
    runtime_paths = _runtime_paths(tmp_path)
    monkeypatch.setattr(config_module, "get_runtime_paths", lambda: runtime_paths)

    cfg = default_config()

    assert cfg.db_path == str(runtime_paths.db_path)
    assert cfg.rss_service.data_dir == str(runtime_paths.rss_service_data_dir)
    assert cfg.rss_service.log_file == str(runtime_paths.rss_service_log_path)
    assert cfg.output.briefing_dir == str(runtime_paths.output_dir)
    assert cfg.output.raw_archive_dir == str(runtime_paths.raw_archive_dir)


def test_dev_runtime_paths_use_build_runtime_dir(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(runtime_paths_module, "is_frozen_app", lambda: False)
    monkeypatch.setattr(runtime_paths_module, "get_repo_root", lambda: tmp_path)

    paths = runtime_paths_module.get_runtime_paths()

    assert paths.bundled_wewe_rss_runtime_dir == tmp_path / "build" / "wewe-rss-runtime"


def test_ensure_config_migrates_legacy_relative_paths_in_frozen_mode(monkeypatch, tmp_path) -> None:
    runtime_paths = _runtime_paths(tmp_path)
    runtime_paths = RuntimePaths(
        state_dir=tmp_path / "AppData" / "Roaming" / "GZHReader",
        config_path=tmp_path / "AppData" / "Roaming" / "GZHReader" / "config.yaml",
        data_dir=tmp_path / "AppData" / "Roaming" / "GZHReader" / "data",
        db_path=tmp_path / "AppData" / "Roaming" / "GZHReader" / "data" / "gzhreader.db",
        rss_service_dir=tmp_path / "AppData" / "Roaming" / "GZHReader" / "wewe-rss",
        rss_service_data_dir=tmp_path / "AppData" / "Roaming" / "GZHReader" / "wewe-rss" / "data",
        rss_service_db_path=tmp_path / "AppData" / "Roaming" / "GZHReader" / "wewe-rss" / "data" / "wewe-rss.db",
        rss_service_pid_file=tmp_path / "AppData" / "Roaming" / "GZHReader" / "wewe-rss" / "wewe-rss.pid",
        rss_service_log_path=tmp_path / "AppData" / "Roaming" / "GZHReader" / "logs" / "wewe-rss.log",
        output_dir=tmp_path / "Documents" / "GZHReader",
        raw_archive_dir=tmp_path / "AppData" / "Roaming" / "GZHReader" / "output" / "raw",
        logs_dir=tmp_path / "AppData" / "Roaming" / "GZHReader" / "logs",
        resource_dir=tmp_path,
        bundled_wewe_rss_source_dir=tmp_path / "third_party" / "wewe-rss",
        bundled_wewe_rss_runtime_dir=tmp_path / "bundle" / "wewe-rss-runtime",
    )
    monkeypatch.setattr(config_module, "is_frozen_app", lambda: True)
    monkeypatch.setattr(config_module, "get_runtime_paths", lambda: runtime_paths)

    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
source:
  mode: aggregate
  url: http://127.0.0.1:4000/feeds/all.atom
db_path: ./data/gzhreader.db
wewe_rss:
  auth_code: '123567'
output:
  briefing_dir: ./output/briefings
  raw_archive_dir: ./output/raw
""".strip(),
        encoding="utf-8",
    )

    cfg = ensure_config(config_path)

    assert cfg.db_path == str(runtime_paths.db_path)
    assert cfg.rss_service.data_dir == str(runtime_paths.rss_service_data_dir)
    assert cfg.rss_service.log_file == str(runtime_paths.rss_service_log_path)
    assert cfg.output.briefing_dir == str(runtime_paths.output_dir)
    assert cfg.output.raw_archive_dir == str(runtime_paths.raw_archive_dir)
    assert (tmp_path / "config.yaml.legacy-reset.bak").exists()


def test_build_schedule_command_uses_python_module_in_dev(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(runtime_paths_module, "is_frozen_app", lambda: False)
    monkeypatch.setattr(runtime_paths_module, "get_repo_root", lambda: tmp_path)
    monkeypatch.setattr(runtime_paths_module.sys, "executable", str(tmp_path / "python.exe"))

    command = build_schedule_command(tmp_path / "config.yaml")

    assert command.executable == (tmp_path / "python.exe")
    assert command.arguments == ["-m", "gzhreader", "run", "today", "--config", str((tmp_path / "config.yaml").resolve())]
    assert command.working_dir == tmp_path


def test_build_schedule_command_uses_console_exe_when_frozen(monkeypatch, tmp_path) -> None:
    console_exe = tmp_path / "GZHReader Console.exe"
    monkeypatch.setattr(runtime_paths_module, "is_frozen_app", lambda: True)
    monkeypatch.setattr(runtime_paths_module, "get_console_executable_path", lambda: console_exe)
    monkeypatch.setattr(runtime_paths_module.sys, "executable", str(tmp_path / "GZHReader.exe"))

    command = build_schedule_command(tmp_path / "config.yaml")

    assert command.executable == console_exe
    assert command.arguments == ["run", "today", "--config", str((tmp_path / "config.yaml").resolve())]
    assert command.working_dir == console_exe.parent
