from __future__ import annotations

import sys

from gzhreader.cli import app, run_gui_server


def main() -> None:
    if len(sys.argv) > 1:
        app(prog_name="GZHReader")
        return
    run_gui_server()


if __name__ == "__main__":
    main()
