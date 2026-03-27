import logging
from logging.handlers import RotatingFileHandler

from gzhreader import logging_utils
from gzhreader.runtime_paths import RuntimePaths


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


def test_build_log_handlers_skips_missing_stderr_and_keeps_file_handler(monkeypatch, tmp_path) -> None:
    runtime_paths = _runtime_paths(tmp_path)
    monkeypatch.setattr(logging_utils, "is_frozen_app", lambda: True)
    monkeypatch.setattr(logging_utils, "ensure_runtime_dirs", lambda: runtime_paths)
    monkeypatch.setattr(logging_utils.sys, "stderr", None)

    handlers = logging_utils._build_log_handlers()

    assert any(isinstance(handler, RotatingFileHandler) for handler in handlers)
    assert not any(type(handler) is logging.StreamHandler for handler in handlers)
