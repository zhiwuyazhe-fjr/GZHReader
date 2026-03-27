from __future__ import annotations

import subprocess
from pathlib import Path

from .config import AppConfig
from .platform_utils import hidden_process_kwargs
from .runtime_paths import build_schedule_command, get_script_path

TASK_NAME = "GZHReaderDaily"


def install_schedule(config: AppConfig, config_path: Path) -> str:
    script = get_script_path("register_task.ps1")
    schedule_command = build_schedule_command(config_path)
    args = [
        "powershell.exe",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-CommandExe",
        str(schedule_command.executable),
        "-CommandArgs",
        subprocess.list2cmdline(schedule_command.arguments),
        "-WorkingDirectory",
        str(schedule_command.working_dir),
        "-RunTime",
        config.schedule.daily_time,
        "-TaskName",
        TASK_NAME,
    ]
    completed = subprocess.run(args, capture_output=True, text=True, check=True, **hidden_process_kwargs())
    return completed.stdout.strip() or completed.stderr.strip()


def remove_schedule() -> str:
    script = get_script_path("unregister_task.ps1")
    args = [
        "powershell.exe",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-TaskName",
        TASK_NAME,
    ]
    completed = subprocess.run(args, capture_output=True, text=True, check=True, **hidden_process_kwargs())
    return completed.stdout.strip() or completed.stderr.strip()


def get_schedule_status(task_name: str = TASK_NAME) -> tuple[bool, str]:
    completed = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", task_name, "/FO", "LIST", "/V"],
        capture_output=True,
        text=True,
        **hidden_process_kwargs(),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "task query failed").strip()
        return False, f"not installed: {detail}"

    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    next_run = next(
        (
            line.split(":", 1)[1].strip()
            for line in lines
            if line.startswith("\u4e0b\u6b21\u8fd0\u884c\u65f6\u95f4:".encode("utf-8").decode("unicode_escape")) or line.startswith("Next Run Time:")
        ),
        "",
    )
    status = next(
        (
            line.split(":", 1)[1].strip()
            for line in lines
            if line.startswith("\u72b6\u6001:".encode("utf-8").decode("unicode_escape")) or line.startswith("Status:")
        ),
        "unknown",
    )
    detail = f"task installed: {status}"
    if next_run:
        detail += f"; next run: {next_run}"
    return True, detail
