# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_dynamic_libs, collect_submodules

project_root = Path(SPEC).resolve().parents[2]
src_root = project_root / "src"
gzhreader_root = src_root / "gzhreader"
assets_root = project_root / "packaging" / "assets"
app_icon = assets_root / "gzhreader.ico"
wewe_rss_runtime_root = project_root / "build" / "wewe-rss-runtime"

site_packages = next((Path(p) for p in sys.path if p and p.endswith("site-packages")), None)
mypyc_binaries = []
if site_packages is not None:
    mypyc_binaries = [(str(path), ".") for path in site_packages.glob("*__mypyc*.pyd")]

common_binaries = collect_dynamic_libs("chardet") + mypyc_binaries

common_datas = [
    (str(gzhreader_root / "templates"), "gzhreader/templates"),
    (str(gzhreader_root / "static"), "gzhreader/static"),
    (str(project_root / "scripts" / "register_task.ps1"), "scripts"),
    (str(project_root / "scripts" / "unregister_task.ps1"), "scripts"),
    (str(project_root / "THIRD_PARTY_NOTICES.md"), "."),
]
if wewe_rss_runtime_root.exists():
    common_datas.append((str(wewe_rss_runtime_root), "wewe-rss-runtime"))
common_hiddenimports = sorted(
    set(
        collect_submodules("uvicorn")
        + collect_submodules("fastapi")
        + collect_submodules("jinja2")
        + collect_submodules("gzhreader")
        + collect_submodules("chardet")
    )
)
common_excludes = ["pytest", "tests", "test", "IPython"]

console_analysis = Analysis(
    [str(gzhreader_root / "console_entry.py")],
    pathex=[str(src_root)],
    binaries=common_binaries,
    datas=common_datas,
    hiddenimports=common_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=common_excludes,
    noarchive=False,
)
console_pyz = PYZ(console_analysis.pure)
console_exe = EXE(
    console_pyz,
    console_analysis.scripts,
    [],
    exclude_binaries=True,
    name="GZHReader Console",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    icon=str(app_icon),
)

gui_analysis = Analysis(
    [str(gzhreader_root / "gui_entry.py")],
    pathex=[str(src_root)],
    binaries=common_binaries,
    datas=common_datas,
    hiddenimports=common_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=common_excludes,
    noarchive=False,
)
gui_pyz = PYZ(gui_analysis.pure)
gui_exe = EXE(
    gui_pyz,
    gui_analysis.scripts,
    [],
    exclude_binaries=True,
    name="GZHReader",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(app_icon),
)

coll = COLLECT(
    gui_exe,
    console_exe,
    gui_analysis.binaries,
    gui_analysis.datas,
    console_analysis.binaries,
    console_analysis.datas,
    strip=False,
    upx=True,
    name="GZHReader",
)
