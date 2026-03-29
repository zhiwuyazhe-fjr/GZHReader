from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "GZHReader"
GUI_EXE_NAME = "GZHReader.exe"
CONSOLE_EXE_NAME = "GZHReader Console.exe"
BUNDLED_RSS_RUNTIME_DIR_NAME = "r"
DEFAULT_GUI_HOST = "127.0.0.1"
DEFAULT_GUI_PORT = 8765


@dataclass(slots=True)
class RuntimePaths:
    state_dir: Path
    config_path: Path
    data_dir: Path
    db_path: Path
    rss_service_dir: Path
    rss_service_data_dir: Path
    rss_service_db_path: Path
    rss_service_pid_file: Path
    rss_service_log_path: Path
    output_dir: Path
    raw_archive_dir: Path
    logs_dir: Path
    resource_dir: Path
    bundled_wewe_rss_source_dir: Path
    bundled_wewe_rss_runtime_dir: Path


@dataclass(slots=True)
class ScheduleCommand:
    executable: Path
    arguments: list[str]
    working_dir: Path


def is_frozen_app() -> bool:
    return bool(getattr(sys, "frozen", False))


def get_repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def get_resource_root() -> Path:
    bundled_root = getattr(sys, "_MEIPASS", None)
    if bundled_root:
        return Path(bundled_root)
    if is_frozen_app():
        return Path(sys.executable).resolve().parent
    return get_repo_root()


def get_state_root() -> Path:
    if not is_frozen_app():
        return get_repo_root()
    appdata = os.environ.get("APPDATA")
    base_dir = Path(appdata) if appdata else (Path.home() / "AppData" / "Roaming")
    return base_dir / APP_NAME


def get_documents_root() -> Path:
    if not is_frozen_app():
        return get_repo_root() / "output" / "briefings"
    return Path.home() / "Documents" / APP_NAME


def get_runtime_paths() -> RuntimePaths:
    state_dir = get_state_root()
    resource_dir = get_resource_root()
    if is_frozen_app():
        data_dir = state_dir / "data"
        rss_service_dir = state_dir / "wewe-rss"
        output_dir = get_documents_root()
        raw_archive_dir = state_dir / "output" / "raw"
        logs_dir = state_dir / "logs"
        config_path = state_dir / "config.yaml"
    else:
        repo_root = get_repo_root()
        data_dir = repo_root / "data"
        rss_service_dir = repo_root / ".runtime" / "wewe-rss"
        output_dir = repo_root / "output" / "briefings"
        raw_archive_dir = repo_root / "output" / "raw"
        logs_dir = repo_root / "logs"
        config_path = repo_root / "config.yaml"

    db_path = data_dir / "gzhreader.db"
    rss_service_data_dir = rss_service_dir / "data"
    rss_service_db_path = rss_service_data_dir / "wewe-rss.db"
    rss_service_pid_file = rss_service_dir / "wewe-rss.pid"
    rss_service_log_path = logs_dir / "wewe-rss.log"
    bundled_wewe_rss_source_dir = get_repo_root() / "third_party" / "wewe-rss"
    bundled_wewe_rss_runtime_dir = get_bundled_rss_runtime_dir()
    return RuntimePaths(
        state_dir=state_dir,
        config_path=config_path,
        data_dir=data_dir,
        db_path=db_path,
        rss_service_dir=rss_service_dir,
        rss_service_data_dir=rss_service_data_dir,
        rss_service_db_path=rss_service_db_path,
        rss_service_pid_file=rss_service_pid_file,
        rss_service_log_path=rss_service_log_path,
        output_dir=output_dir,
        raw_archive_dir=raw_archive_dir,
        logs_dir=logs_dir,
        resource_dir=resource_dir,
        bundled_wewe_rss_source_dir=bundled_wewe_rss_source_dir,
        bundled_wewe_rss_runtime_dir=bundled_wewe_rss_runtime_dir,
    )


def ensure_runtime_dirs(paths: RuntimePaths | None = None) -> RuntimePaths:
    runtime_paths = paths or get_runtime_paths()
    for directory in (
        runtime_paths.state_dir,
        runtime_paths.data_dir,
        runtime_paths.rss_service_dir,
        runtime_paths.rss_service_data_dir,
        runtime_paths.output_dir,
        runtime_paths.logs_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)
    return runtime_paths


def get_default_config_path() -> Path:
    return get_runtime_paths().config_path


def resolve_config_path(config_path: Path | None) -> Path:
    if config_path is None:
        return get_default_config_path()
    return config_path.expanduser().resolve() if config_path.as_posix() != "." else config_path.resolve()


def get_script_path(script_name: str) -> Path:
    return get_resource_root() / "scripts" / script_name


def get_bundled_rss_runtime_dir() -> Path:
    resource_root = get_resource_root()
    candidates: list[Path] = []
    if is_frozen_app():
        candidates.extend(
            [
                Path(sys.executable).resolve().parent / BUNDLED_RSS_RUNTIME_DIR_NAME,
                resource_root / BUNDLED_RSS_RUNTIME_DIR_NAME,
            ]
        )
    else:
        candidates.append(get_repo_root() / "build" / "wewe-rss-runtime")

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def get_console_executable_path() -> Path | None:
    if not is_frozen_app():
        return None
    current = Path(sys.executable).resolve()
    if current.name.lower() == CONSOLE_EXE_NAME.lower():
        return current
    sibling = current.with_name(CONSOLE_EXE_NAME)
    if sibling.exists():
        return sibling
    return current


def get_gui_executable_path() -> Path | None:
    if not is_frozen_app():
        return None
    current = Path(sys.executable).resolve()
    if current.name.lower() == GUI_EXE_NAME.lower():
        return current
    sibling = current.with_name(GUI_EXE_NAME)
    if sibling.exists():
        return sibling
    return current


def build_schedule_command(config_path: Path) -> ScheduleCommand:
    resolved_config = config_path.expanduser().resolve()
    if is_frozen_app():
        executable = get_console_executable_path() or Path(sys.executable).resolve()
        return ScheduleCommand(
            executable=executable,
            arguments=["run", "today", "--config", str(resolved_config)],
            working_dir=executable.parent,
        )
    return ScheduleCommand(
        executable=Path(sys.executable).resolve(),
        arguments=["-m", "gzhreader", "run", "today", "--config", str(resolved_config)],
        working_dir=get_repo_root(),
    )
