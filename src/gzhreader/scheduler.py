from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from .config import AppConfig

TASK_NAME = "GZHReaderDaily"


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


def get_schedule_status(task_name: str = TASK_NAME) -> tuple[bool, str]:
    completed = subprocess.run(
        ["schtasks.exe", "/Query", "/TN", task_name, "/FO", "LIST", "/V"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "未安装计划任务").strip()
        return False, f"未安装：{detail}"

    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    next_run = next((line.split(":", 1)[1].strip() for line in lines if line.startswith("下次运行时间:") or line.startswith("Next Run Time:")), "")
    status = next((line.split(":", 1)[1].strip() for line in lines if line.startswith("状态:") or line.startswith("Status:")), "已安装")
    detail = f"已安装，状态：{status}"
    if next_run:
        detail += f"，下次运行：{next_run}"
    return True, detail
