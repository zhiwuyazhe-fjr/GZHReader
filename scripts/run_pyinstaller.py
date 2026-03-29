from __future__ import annotations

import os
import platform
import sys


def _patch_windows_platform() -> None:
    """Avoid very slow platform probes on some Windows environments during PyInstaller startup."""
    if os.name != "nt":
        return

    release = os.environ.get("GZHREADER_BUILD_WINDOWS_RELEASE", "10")
    machine = os.environ.get("PROCESSOR_ARCHITECTURE") or "AMD64"

    platform.system = lambda *args, **kwargs: "Windows"  # type: ignore[assignment]
    platform.win32_ver = lambda *args, **kwargs: (release, "", "", "")  # type: ignore[assignment]
    platform.machine = lambda *args, **kwargs: machine  # type: ignore[assignment]


def main() -> int:
    _patch_windows_platform()
    import PyInstaller.__main__

    PyInstaller.__main__.run(sys.argv[1:])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
