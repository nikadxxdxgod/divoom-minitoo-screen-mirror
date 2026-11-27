"""Displays as Windows sees them: adapter names, virtual displays and modes.

Used for "MiniToo as an extra screen": Windows can only extend the desktop
onto a display it knows, so the MiniToo needs a virtual display (an
Indirect Display driver, installed separately by the user). This module
finds such a display, lists its modes and can switch it to a 5:4 mode - the
shape of the 160x128 screen - with the ordinary ChangeDisplaySettingsEx API
(the same thing Windows Settings > Display does; it can be changed back
there at any time). It never installs anything.
"""

from __future__ import annotations

import ctypes
import re
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

IS_WINDOWS = sys.platform == "win32"

# words in adapter / monitor names of virtual display drivers
VIRTUAL_HINTS = ("virtual", "idd", "iddsample", "indirect", "parsec", "usbmm", "vdd",
                 "spacedesk", "deskreen", "duet", "splashtop", "mtt")

DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x1
ENUM_CURRENT_SETTINGS = -1
DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000
DM_DISPLAYFREQUENCY = 0x00400000
CDS_UPDATEREGISTRY = 0x1
CDS_TEST = 0x2
DISP_CHANGE_SUCCESSFUL = 0
DISP_CHANGE_RESTART = 1


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32),
                ("DeviceString", wintypes.WCHAR * 128), ("StateFlags", wintypes.DWORD),
                ("DeviceID", wintypes.WCHAR * 128), ("DeviceKey", wintypes.WCHAR * 128)]


class DEVMODEW(ctypes.Structure):
    """DEVMODEW, display variant (220 bytes)."""
    _fields_ = [("dmDeviceName", wintypes.WCHAR * 32), ("dmSpecVersion", wintypes.WORD),
                ("dmDriverVersion", wintypes.WORD), ("dmSize", wintypes.WORD),
                ("dmDriverExtra", wintypes.WORD), ("dmFields", wintypes.DWORD),
                ("dmPositionX", wintypes.LONG), ("dmPositionY", wintypes.LONG),
                ("dmDisplayOrientation", wintypes.DWORD), ("dmDisplayFixedOutput", wintypes.DWORD),
                ("dmColor", ctypes.c_short), ("dmDuplex", ctypes.c_short),
                ("dmYResolution", ctypes.c_short), ("dmTTOption", ctypes.c_short),
                ("dmCollate", ctypes.c_short), ("dmFormName", wintypes.WCHAR * 32),
                ("dmLogPixels", wintypes.WORD), ("dmBitsPerPel", wintypes.DWORD),
                ("dmPelsWidth", wintypes.DWORD), ("dmPelsHeight", wintypes.DWORD),
                ("dmDisplayFlags", wintypes.DWORD), ("dmDisplayFrequency", wintypes.DWORD),
                ("dmICMMethod", wintypes.DWORD), ("dmICMIntent", wintypes.DWORD),
                ("dmMediaType", wintypes.DWORD), ("dmDitherType", wintypes.DWORD),
                ("dmReserved1", wintypes.DWORD), ("dmReserved2", wintypes.DWORD),
                ("dmPanningWidth", wintypes.DWORD), ("dmPanningHeight", wintypes.DWORD)]


if IS_WINDOWS:
    _user32 = ctypes.WinDLL("user32", use_last_error=True)
    _user32.EnumDisplayDevicesW.restype = wintypes.BOOL
    _user32.EnumDisplayDevicesW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                            ctypes.POINTER(DISPLAY_DEVICEW), wintypes.DWORD]
    _user32.EnumDisplaySettingsW.restype = wintypes.BOOL
    _user32.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                             ctypes.POINTER(DEVMODEW)]
    _user32.ChangeDisplaySettingsExW.restype = wintypes.LONG
    _user32.ChangeDisplaySettingsExW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(DEVMODEW),
                                                 wintypes.HWND, wintypes.DWORD, ctypes.c_void_p]


@dataclass
class DisplayDevice:
    device: str          # \\.\DISPLAY3 - the same as capture.MonitorInfo.name
    adapter: str         # e.g. "Intel(R) HD Graphics 520" or "Virtual Display Driver"
    monitor: str         # e.g. "Generic PnP Monitor"
    attached: bool

    @property
    def virtual(self) -> bool:
        return looks_virtual(self.adapter) or looks_virtual(self.monitor)


def looks_virtual(name: str) -> bool:
    n = (name or "").lower()
    # short hints only as whole words ("idd" must not match "hidden")
    return any(re.search(r"\b" + h + r"\b", n) if len(h) <= 4 else h in n for h in VIRTUAL_HINTS)


def display_devices() -> Dict[str, DisplayDevice]:
    """All display outputs, keyed by device name (\\\\.\\DISPLAYn)."""
    out: Dict[str, DisplayDevice] = {}
    if not IS_WINDOWS:
        return out
    i = 0
    while True:
        dd = DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(dd)
        if not _user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        i += 1
        mon = DISPLAY_DEVICEW()
        mon.cb = ctypes.sizeof(mon)
        monitor = ""
        if _user32.EnumDisplayDevicesW(dd.DeviceName, 0, ctypes.byref(mon), 0):
            monitor = mon.DeviceString
        out[dd.DeviceName] = DisplayDevice(dd.DeviceName, dd.DeviceString, monitor,
                                           bool(dd.StateFlags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP))
    return out


def adapter_names() -> List[str]:
    """Names of the graphics adapters that drive a display (no duplicates)."""
    names: List[str] = []
    for d in display_devices().values():
        if d.attached and d.adapter and not d.virtual and d.adapter not in names:
            names.append(d.adapter)
    return names


Mode = Tuple[int, int, int]          # width, height, refresh rate


def list_modes(device: str) -> List[Mode]:
    modes: List[Mode] = []
    if not IS_WINDOWS:
        return modes
    i = 0
    while True:
        dm = DEVMODEW()
        dm.dmSize = ctypes.sizeof(dm)
        if not _user32.EnumDisplaySettingsW(device, i, ctypes.byref(dm)):
            break
        i += 1
        m = (int(dm.dmPelsWidth), int(dm.dmPelsHeight), int(dm.dmDisplayFrequency))
        if m not in modes:
            modes.append(m)
    return modes


def current_mode(device: str) -> Optional[Mode]:
    if not IS_WINDOWS:
        return None
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(dm)
    if not _user32.EnumDisplaySettingsW(device, ctypes.c_uint32(ENUM_CURRENT_SETTINGS).value,
                                        ctypes.byref(dm)):
        return None
    return int(dm.dmPelsWidth), int(dm.dmPelsHeight), int(dm.dmDisplayFrequency)


def is_minitoo_shape(w: int, h: int) -> bool:
    """5:4 - the shape of the 160x128 screen."""
    return w > 0 and h > 0 and w * 4 == h * 5


# preferred sizes: big enough for Windows, small enough that text stays
# readable after shrinking to 160x128
PREFERRED_SIZES = [(800, 640), (640, 512), (960, 768), (1280, 1024), (1000, 800), (720, 576)]


def best_minitoo_mode(modes: List[Mode]) -> Optional[Tuple[int, int]]:
    sizes = {(w, h) for w, h, _hz in modes if is_minitoo_shape(w, h)}
    for s in PREFERRED_SIZES:
        if s in sizes:
            return s
    if not sizes:
        return None
    # otherwise the one closest to 800x640
    return min(sizes, key=lambda s: abs(s[0] - 800))


def set_mode(device: str, width: int, height: int) -> int:
    """Switch a display to width x height (keeps the refresh rate if
    possible). Returns the DISP_CHANGE_* code (0 = done)."""
    if not IS_WINDOWS:
        return -1
    dm = DEVMODEW()
    dm.dmSize = ctypes.sizeof(dm)
    if not _user32.EnumDisplaySettingsW(device, ctypes.c_uint32(ENUM_CURRENT_SETTINGS).value,
                                        ctypes.byref(dm)):
        return -1
    dm.dmPelsWidth, dm.dmPelsHeight = width, height
    dm.dmFields = DM_PELSWIDTH | DM_PELSHEIGHT
    code = _user32.ChangeDisplaySettingsExW(device, ctypes.byref(dm), None, CDS_TEST, None)
    if code != DISP_CHANGE_SUCCESSFUL:
        return int(code)
    return int(_user32.ChangeDisplaySettingsExW(device, ctypes.byref(dm), None,
                                                CDS_UPDATEREGISTRY, None))
