from __future__ import annotations

import os
import subprocess
import webbrowser
from pathlib import Path


def is_windows() -> bool:
    return os.name == "nt"


def hidden_process_kwargs() -> dict[str, object]:
    if not is_windows():
        return {}

    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {
        "startupinfo": startupinfo,
        "creationflags": creationflags,
    }


def open_local_path(path: Path) -> None:
    resolved = path.resolve()
    if hasattr(os, "startfile"):
        os.startfile(str(resolved))  # type: ignore[attr-defined]
        return
    webbrowser.open(resolved.as_uri())


def open_web_url(url: str) -> None:
    webbrowser.open(url)
