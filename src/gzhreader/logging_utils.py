from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from .runtime_paths import ensure_runtime_dirs, is_frozen_app


def _has_usable_stream(stream: object) -> bool:
    return stream is not None and hasattr(stream, "write")


def _build_log_handlers() -> list[logging.Handler]:
    handlers: list[logging.Handler] = []

    if _has_usable_stream(sys.stderr):
        handlers.append(logging.StreamHandler())

    if is_frozen_app():
        runtime_paths = ensure_runtime_dirs()
        runtime_paths.logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = runtime_paths.logs_dir / "gzhreader.log"
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=1_048_576,
                backupCount=3,
                encoding="utf-8",
            )
        )

    if not handlers:
        handlers.append(logging.NullHandler())

    return handlers


def configure_logging(level: str = "INFO") -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=_build_log_handlers(),
        force=True,
    )
