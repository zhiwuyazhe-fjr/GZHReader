from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from .config import AppConfig


def install_schedule(config: AppConfig, config_path: Path) -> str:
    script = Path("scripts/register_task.ps1")
    python_exe = Path(sys.executable)
    args = [
        "powershell.exe",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-PythonExe",
        str(python_exe),
        "-ProjectDir",
        str(Path.cwd()),
        "-RunTime",
        config.schedule.daily_time,
        "-ConfigPath",
        str(config_path.resolve()),
    ]
    completed = subprocess.run(args, capture_output=True, text=True, check=True)
    return completed.stdout.strip() or completed.stderr.strip()


def remove_schedule() -> str:
    script = Path("scripts/unregister_task.ps1")
    args = ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    completed = subprocess.run(args, capture_output=True, text=True, check=True)
    return completed.stdout.strip() or completed.stderr.strip()
