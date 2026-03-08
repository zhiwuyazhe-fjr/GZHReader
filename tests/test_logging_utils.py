import logging
from logging.handlers import RotatingFileHandler

from gzhreader import logging_utils
from gzhreader.runtime_paths import RuntimePaths


def test_build_log_handlers_skips_missing_stderr_and_keeps_file_handler(monkeypatch, tmp_path) -> None:
    runtime_paths = RuntimePaths(
        state_dir=tmp_path / "state",
        config_path=tmp_path / "state" / "config.yaml",
        data_dir=tmp_path / "state" / "data",
        db_path=tmp_path / "state" / "data" / "gzhreader.db",
        infra_dir=tmp_path / "state" / "infra",
        wewe_rss_dir=tmp_path / "state" / "infra" / "wewe-rss",
        output_dir=tmp_path / "Documents" / "GZHReader",
        raw_archive_dir=tmp_path / "state" / "output" / "raw",
        logs_dir=tmp_path / "state" / "logs",
        resource_dir=tmp_path,
    )
    monkeypatch.setattr(logging_utils, "is_frozen_app", lambda: True)
    monkeypatch.setattr(logging_utils, "ensure_runtime_dirs", lambda: runtime_paths)
    monkeypatch.setattr(logging_utils.sys, "stderr", None)

    handlers = logging_utils._build_log_handlers()

    assert any(isinstance(handler, RotatingFileHandler) for handler in handlers)
    assert not any(type(handler) is logging.StreamHandler for handler in handlers)
