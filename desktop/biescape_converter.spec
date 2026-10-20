# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir spec for BIEscape Converter."""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files

block_cipher = None

datas = []
binaries = []
hiddenimports = ["tableauhyperapi", "openpyxl", "engine", "engine.extract", "engine.hyper", "engine.parser", "engine.schema"]

try:
    tmp_ret = collect_all("tableauhyperapi")
    datas += tmp_ret[0]
    binaries += tmp_ret[1]
    hiddenimports += tmp_ret[2]
except Exception:
    datas += collect_data_files("tableauhyperapi")

# SPECPATH is the directory containing this .spec file (desktop/)
spec_dir = Path(SPECPATH).resolve()
root = spec_dir.parent
datas += [
    (str(root / "engine"), "engine"),
]

a = Analysis(
    [str(spec_dir / "biescape_converter.py")],
    pathex=[str(root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="BIEscape-Converter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="BIEscape-Converter",
)
