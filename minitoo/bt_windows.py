"""Windows Bluetooth (Classic) - adapter, device discovery, COM ports.

Uses Windows' built-in BluetoothAPIs.dll through ctypes (no extra package).
The MiniToo uses Bluetooth Classic (RFCOMM), so no BLE scanning is needed.
"""

from __future__ import annotations

import ctypes
import os
import re
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import List, Optional

from .i18n import _

IS_WINDOWS = sys.platform == "win32"
MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
DIVOOM_HINTS = ("divoom", "minitoo", "tivoo", "tiivoo")


def normalize_mac(text: str) -> Optional[str]:
    """"aa-bb-cc-dd-ee-ff" / "AABBCCDDEEFF" -> "AA:BB:CC:DD:EE:FF"."""
    t = (text or "").strip().replace("-", ":").upper()
    if MAC_RE.match(t):
        return t
    bare = re.sub(r"[^0-9A-Fa-f]", "", text or "").upper()
    if len(bare) == 12:
        return ":".join(bare[i:i + 2] for i in range(0, 12, 2))
    return None


def mac_from_int(value: int) -> str:
    return ":".join(f"{(value >> (8 * i)) & 0xFF:02X}" for i in range(5, -1, -1))


@dataclass
class BtDevice:
    address: str
    name: str
    paired: bool = False        # fAuthenticated
    remembered: bool = False    # fRemembered (known to Windows)
    connected: bool = False
    device_class: int = 0

    @property
    def is_divoom(self) -> bool:
        n = self.name.lower()
        return any(h in n for h in DIVOOM_HINTS)

    @property
    def is_audio_class(self) -> bool:
        return ((self.device_class >> 8) & 0x1F) == 0x04   # Major class: Audio/Video

    @property
    def score(self) -> int:
        """Which entry is most likely the MiniToo SPP side ([B]: "Divoom MiniToo-Audio")."""
        n = self.name.lower()
        s = 0
        if self.is_divoom:
            s += 10
        if "minitoo" in n:
            s += 10
        if "audio" in n:
            s += 3
        if self.paired:
            s += 2
        return s

    def label(self) -> str:
        flags = []
        if self.connected:
            flags.append(_("connected"))
        if self.paired:
            flags.append(_("paired"))
        elif not self.remembered:
            flags.append(_("new"))
        extra = f" · {', '.join(flags)}" if flags else ""
        return f"{self.name or _('(no name)')}  [{self.address}]{extra}"


@dataclass
class BtRadio:
    name: str
    address: str
    manufacturer: int
    lmp_subversion: int


# ---------------------------------------------------------------------------
# ctypes structures (bluetoothapis.h)
# ---------------------------------------------------------------------------

BLUETOOTH_MAX_NAME_SIZE = 248
ERROR_NO_MORE_ITEMS = 259


class SYSTEMTIME(ctypes.Structure):
    _fields_ = [(n, wintypes.WORD) for n in ("wYear", "wMonth", "wDayOfWeek", "wDay",
                                              "wHour", "wMinute", "wSecond", "wMilliseconds")]


class BLUETOOTH_DEVICE_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("Address", ctypes.c_ulonglong),
        ("ulClassofDevice", wintypes.ULONG),
        ("fConnected", wintypes.BOOL),
        ("fRemembered", wintypes.BOOL),
        ("fAuthenticated", wintypes.BOOL),
        ("stLastSeen", SYSTEMTIME),
        ("stLastUsed", SYSTEMTIME),
        ("szName", wintypes.WCHAR * BLUETOOTH_MAX_NAME_SIZE),
    ]


class BLUETOOTH_DEVICE_SEARCH_PARAMS(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("fReturnAuthenticated", wintypes.BOOL),
        ("fReturnRemembered", wintypes.BOOL),
        ("fReturnUnknown", wintypes.BOOL),
        ("fReturnConnected", wintypes.BOOL),
        ("fIssueInquiry", wintypes.BOOL),
        ("cTimeoutMultiplier", ctypes.c_ubyte),
        ("hRadio", wintypes.HANDLE),
    ]


class BLUETOOTH_FIND_RADIO_PARAMS(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD)]


class BLUETOOTH_RADIO_INFO(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("address", ctypes.c_ulonglong),
        ("szName", wintypes.WCHAR * BLUETOOTH_MAX_NAME_SIZE),
        ("ulClassofDevice", wintypes.ULONG),
        ("lmpSubversion", wintypes.USHORT),
        ("manufacturer", wintypes.USHORT),
    ]


_api = None


def _bt_api():
    global _api
    if _api is not None:
        return _api
    if not IS_WINDOWS:
        raise OSError("The Bluetooth API is only available on Windows")
    last_err = None
    for dll in ("BluetoothApis.dll", "bthprops.cpl"):
        try:
            lib = ctypes.WinDLL(dll, use_last_error=True)
            break
        except OSError as e:  # pragma: no cover
            last_err = e
    else:  # pragma: no cover
        raise OSError(f"Bluetooth API could not be loaded: {last_err}")

    lib.BluetoothFindFirstRadio.argtypes = [ctypes.POINTER(BLUETOOTH_FIND_RADIO_PARAMS),
                                            ctypes.POINTER(wintypes.HANDLE)]
    lib.BluetoothFindFirstRadio.restype = wintypes.HANDLE
    lib.BluetoothFindNextRadio.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.HANDLE)]
    lib.BluetoothFindNextRadio.restype = wintypes.BOOL
    lib.BluetoothFindRadioClose.argtypes = [wintypes.HANDLE]
    lib.BluetoothFindRadioClose.restype = wintypes.BOOL
    lib.BluetoothGetRadioInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(BLUETOOTH_RADIO_INFO)]
    lib.BluetoothGetRadioInfo.restype = wintypes.DWORD
    lib.BluetoothFindFirstDevice.argtypes = [ctypes.POINTER(BLUETOOTH_DEVICE_SEARCH_PARAMS),
                                             ctypes.POINTER(BLUETOOTH_DEVICE_INFO)]
    lib.BluetoothFindFirstDevice.restype = wintypes.HANDLE
    lib.BluetoothFindNextDevice.argtypes = [wintypes.HANDLE, ctypes.POINTER(BLUETOOTH_DEVICE_INFO)]
    lib.BluetoothFindNextDevice.restype = wintypes.BOOL
    lib.BluetoothFindDeviceClose.argtypes = [wintypes.HANDLE]
    lib.BluetoothFindDeviceClose.restype = wintypes.BOOL
    _api = lib
    return lib


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True) if IS_WINDOWS else None
if _kernel32 is not None:
    _kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel32.CloseHandle.restype = wintypes.BOOL


def list_radios() -> List[BtRadio]:
    """Bluetooth adapters of this PC. Empty list = no adapter or it is off."""
    lib = _bt_api()
    params = BLUETOOTH_FIND_RADIO_PARAMS(ctypes.sizeof(BLUETOOTH_FIND_RADIO_PARAMS))
    h_radio = wintypes.HANDLE()
    h_find = lib.BluetoothFindFirstRadio(ctypes.byref(params), ctypes.byref(h_radio))
    radios: List[BtRadio] = []
    if not h_find:
        return radios
    try:
        while True:
            info = BLUETOOTH_RADIO_INFO()
            info.dwSize = ctypes.sizeof(BLUETOOTH_RADIO_INFO)
            if lib.BluetoothGetRadioInfo(h_radio, ctypes.byref(info)) == 0:
                radios.append(BtRadio(info.szName, mac_from_int(info.address),
                                      info.manufacturer, info.lmpSubversion))
            _kernel32.CloseHandle(h_radio)
            h_radio = wintypes.HANDLE()
            if not lib.BluetoothFindNextRadio(h_find, ctypes.byref(h_radio)):
                break
    finally:
        lib.BluetoothFindRadioClose(h_find)
    return radios


def find_devices(inquiry: bool = False, timeout_multiplier: int = 5) -> List[BtDevice]:
    """inquiry=False - only devices known to Windows (paired), instantly.
    inquiry=True  - also search nearby devices (timeout_multiplier x 1.28 s)."""
    lib = _bt_api()
    params = BLUETOOTH_DEVICE_SEARCH_PARAMS()
    params.dwSize = ctypes.sizeof(BLUETOOTH_DEVICE_SEARCH_PARAMS)
    params.fReturnAuthenticated = True
    params.fReturnRemembered = True
    params.fReturnConnected = True
    params.fReturnUnknown = bool(inquiry)
    params.fIssueInquiry = bool(inquiry)
    params.cTimeoutMultiplier = max(1, min(48, int(timeout_multiplier))) if inquiry else 0
    params.hRadio = None

    info = BLUETOOTH_DEVICE_INFO()
    info.dwSize = ctypes.sizeof(BLUETOOTH_DEVICE_INFO)
    h_find = lib.BluetoothFindFirstDevice(ctypes.byref(params), ctypes.byref(info))
    found = {}
    if not h_find:
        err = ctypes.get_last_error()
        if err in (0, ERROR_NO_MORE_ITEMS):
            return []
        raise OSError(err, f"Bluetooth search error (Windows code {err})")
    try:
        while True:
            dev = BtDevice(address=mac_from_int(info.Address), name=info.szName,
                           paired=bool(info.fAuthenticated), remembered=bool(info.fRemembered),
                           connected=bool(info.fConnected), device_class=info.ulClassofDevice)
            found[dev.address] = dev
            info = BLUETOOTH_DEVICE_INFO()
            info.dwSize = ctypes.sizeof(BLUETOOTH_DEVICE_INFO)
            if not lib.BluetoothFindNextDevice(h_find, ctypes.byref(info)):
                break
    finally:
        lib.BluetoothFindDeviceClose(h_find)
    return sorted(found.values(), key=lambda d: (-d.score, d.name.lower()))


def bluetooth_socket_supported() -> bool:
    import socket
    return all(hasattr(socket, n) for n in ("AF_BLUETOOTH", "BTPROTO_RFCOMM"))


def open_bluetooth_settings() -> None:
    if IS_WINDOWS:
        os.startfile("ms-settings:bluetooth")  # type: ignore[attr-defined]


def usb_divoom_devices() -> List[str]:
    """Divoom devices connected over USB (names).

    Checked on a real MiniToo (2026-11-26): over USB Windows only sees audio
    (USB Audio "Divoom Audio" + microphone), media keys (HID, input only) and
    a card reader. There is no data channel for the display over USB - the
    picture can only be sent over Bluetooth."""
    if not IS_WINDOWS:
        return []
    try:
        cfg = ctypes.WinDLL("cfgmgr32")
    except OSError:  # pragma: no cover
        return []
    CM_GETIDLIST_FILTER_ENUMERATOR, CM_GETIDLIST_FILTER_PRESENT = 0x1, 0x100
    CM_DRP_DEVICEDESC, CM_DRP_FRIENDLYNAME = 0x1, 0xD
    flags = CM_GETIDLIST_FILTER_ENUMERATOR | CM_GETIDLIST_FILTER_PRESENT
    size = wintypes.ULONG()
    if cfg.CM_Get_Device_ID_List_SizeW(ctypes.byref(size), "USB", flags) != 0 or not size.value:
        return []
    buf = ctypes.create_unicode_buffer(size.value)
    if cfg.CM_Get_Device_ID_ListW("USB", buf, size.value, flags) != 0:
        return []
    ids = [s for s in buf[:size.value].split("\x00") if s]
    names: List[str] = []
    for dev_id in ids:
        if "VID_4C4A" not in dev_id.upper():          # Jieli (the MiniToo chip)
            continue
        inst = wintypes.DWORD()
        if cfg.CM_Locate_DevNodeW(ctypes.byref(inst), dev_id, 0) != 0:
            continue
        for prop in (CM_DRP_FRIENDLYNAME, CM_DRP_DEVICEDESC):
            out = ctypes.create_unicode_buffer(256)
            ln = wintypes.ULONG(ctypes.sizeof(out))
            if cfg.CM_Get_DevNode_Registry_PropertyW(inst.value, prop, None, out,
                                                     ctypes.byref(ln), 0) == 0:
                if "divoom" in out.value.lower() and out.value not in names:
                    names.append(out.value)
    return names


@dataclass
class ComPort:
    device: str
    description: str
    hwid: str
    mac: Optional[str]


def list_bluetooth_com_ports() -> List[ComPort]:
    """When pairing, Windows creates virtual COM ports for SPP services."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    out = []
    for p in list_ports.comports():
        hwid = p.hwid or ""
        if "BTHENUM" not in hwid.upper() and "bluetooth" not in (p.description or "").lower():
            continue
        mac = None
        m = re.search(r"&([0-9A-Fa-f]{12})_", hwid)
        if m and m.group(1) != "000000000000":
            mac = normalize_mac(m.group(1))
        out.append(ComPort(p.device, p.description or "", hwid, mac))
    return out
