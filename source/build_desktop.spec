from pathlib import Path
# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    [str(Path(SPECPATH) / 'desktop.py')],
    pathex=[],
    binaries=[],
    datas=[(str(Path(SPECPATH) / 'checkmark.svg'), '.')]+[(str(Path(SPECPATH).parent / 'plugins' / name), 'plugins/'+name) for name in ('network-monitor','network-proxy')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6.QtWebEngineWidgets', 'PySide6.QtWebEngineCore', 'PySide6.QtQml', 'PySide6.QtQuick'],
    noarchive=False,
    optimize=0,
)
# Qt uses Windows ICU. Never bundle a different ICU from PATH (e.g. Poppler).
a.binaries = [entry for entry in a.binaries if entry[0].lower() not in ('icuuc.dll', 'icudt78.dll')]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TaishanPiManager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=[str(Path(SPECPATH) / 'app.ico')],
)


