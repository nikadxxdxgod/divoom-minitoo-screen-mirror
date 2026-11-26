# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: two EXEs in one folder (dist\MiniTooMirror\)
#   MiniTooMirror.exe   - the program (window)
#   MiniTooDiagnose.exe - Bluetooth diagnostic (console)

EXCLUDES = ["tkinter", "numpy", "pydoc_data", "mss", "winrt",
            "PyQt6.QtWebEngineCore", "PyQt6.QtWebEngineWidgets", "PyQt6.QtQml", "PyQt6.QtQuick",
            "PyQt6.QtMultimedia", "PyQt6.QtNetwork", "PyQt6.QtPdf", "PyQt6.Qt3DCore",
            "PyQt6.QtBluetooth", "PyQt6.QtSql", "PyQt6.QtTest", "PyQt6.QtDesigner"]

app = Analysis(
    ["app.py"],
    pathex=[],
    datas=[("assets", "assets"), ("minitoo/locales", "minitoo/locales")],
    hiddenimports=["minitoo.simulator", "PyQt6.QtSvg", "av"],
    excludes=EXCLUDES,
    noarchive=False,
)
diag = Analysis(
    ["diagnose.py"],
    pathex=[],
    datas=[("minitoo/locales", "minitoo/locales")],
    hiddenimports=[],
    excludes=EXCLUDES + ["PyQt6", "av"],
    noarchive=False,
)

app_exe = EXE(
    PYZ(app.pure),
    app.scripts,
    [],
    exclude_binaries=True,
    name="MiniTooMirror",
    icon="assets/icon.ico",
    console=False,
    upx=False,
)
diag_exe = EXE(
    PYZ(diag.pure),
    diag.scripts,
    [],
    exclude_binaries=True,
    name="MiniTooDiagnose",
    icon="assets/icon.ico",
    console=True,
    upx=False,
)

coll = COLLECT(
    app_exe, app.binaries, app.datas,
    diag_exe, diag.binaries, diag.datas,
    upx=False,
    name="MiniTooMirror",
)
