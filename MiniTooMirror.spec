# -*- mode: python ; coding: utf-8 -*-
# PyInstaller: two EXEs in one folder (dist\MiniTooMirror\)
#   MiniTooMirror.exe   - the program (window)
#   MiniTooDiagnose.exe - Bluetooth diagnostic (console)

import os
from PyInstaller.utils.win32.versioninfo import (FixedFileInfo, StringFileInfo, StringStruct,
                                                 StringTable, VarFileInfo, VarStruct, VSVersionInfo)

_ns = {}
exec(open(os.path.join(SPECPATH, "minitoo", "__init__.py"), encoding="utf-8").read(), _ns)
VERSION = _ns["__version__"]
_v = tuple(int(x) for x in (VERSION.split(".") + ["0", "0", "0"])[:4])


def version_info(description, internal):
    return VSVersionInfo(
        ffi=FixedFileInfo(filevers=_v, prodvers=_v),
        kids=[StringFileInfo([StringTable("040904B0", [
            StringStruct("CompanyName", "MiniToo Mirror contributors"),
            StringStruct("FileDescription", description),
            StringStruct("FileVersion", VERSION),
            StringStruct("InternalName", internal),
            StringStruct("LegalCopyright", "MIT License"),
            StringStruct("OriginalFilename", internal + ".exe"),
            StringStruct("ProductName", "MiniToo Mirror"),
            StringStruct("ProductVersion", VERSION)])]),
            VarFileInfo([VarStruct("Translation", [1033, 1200])])])


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
    version=version_info("MiniToo Mirror — screen mirroring for the Divoom MiniToo", "MiniTooMirror"),
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
    version=version_info("MiniToo Mirror Bluetooth diagnostic", "MiniTooDiagnose"),
)

coll = COLLECT(
    app_exe, app.binaries, app.datas,
    diag_exe, diag.binaries, diag.datas,
    upx=False,
    name="MiniTooMirror",
)
