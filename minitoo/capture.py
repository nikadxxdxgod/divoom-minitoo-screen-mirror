"""Screen capture with Windows GDI (ctypes): a whole display, a region or a window.

The device screen is tiny (160x128), so capturing at full resolution is
wasteful: GDI StretchBlt(HALFTONE) shrinks the picture while capturing to
about 2x the final size - much less CPU and memory. The final, high-quality
resize is done by imaging.process_frame.

Coordinates are physical pixels (the process must be DPI aware; Qt6 does
that itself, other scripts call ensure_dpi_awareness()).
"""

from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PIL import Image

from .i18n import _

IS_WINDOWS = sys.platform == "win32"


class CaptureError(Exception):
    pass


class WindowGone(CaptureError):
    """The window was closed."""


class SourceUnavailable(CaptureError):
    """The source is temporarily unavailable (e.g. the window is minimized)."""


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    @property
    def valid(self) -> bool:
        return self.width > 0 and self.height > 0

    def intersect(self, other: "Rect") -> "Rect":
        l, t = max(self.left, other.left), max(self.top, other.top)
        r, b = min(self.right, other.right), min(self.bottom, other.bottom)
        return Rect(l, t, max(0, r - l), max(0, b - t))

    def to_list(self) -> List[int]:
        return [self.left, self.top, self.width, self.height]

    @classmethod
    def from_list(cls, v) -> Optional["Rect"]:
        try:
            r = cls(*(int(x) for x in v[:4]))
            return r if r.valid else None
        except (TypeError, ValueError):
            return None

    def __str__(self) -> str:
        return f"{self.width}×{self.height} ({self.left}, {self.top})"


@dataclass
class MonitorInfo:
    index: int          # from 1
    rect: Rect
    primary: bool
    name: str

    def label(self) -> str:
        p = f" · {_('primary')}" if self.primary else ""
        return _("Display {n}", n=self.index) + f": {self.rect.width}×{self.rect.height}{p}"


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    process: str
    rect: Rect
    minimized: bool

    def label(self) -> str:
        t = self.title if len(self.title) <= 60 else self.title[:57] + "…"
        m = f" · {_('minimized')}" if self.minimized else ""
        return f"{t}  —  {self.process}{m}"


@dataclass
class CaptureSource:
    kind: str = "monitor"            # "monitor" | "region" | "window"
    monitor: int = 1
    region: Optional[Rect] = None
    hwnd: int = 0
    window_title: str = ""
    window_process: str = ""
    client_only: bool = True         # without the window frame / title bar
    method: str = "screen"           # "screen" (what is visible) | "printwindow" (even if covered)

    def label(self) -> str:
        if self.kind == "region" and self.region:
            return _("Region {r}", r=self.region)
        if self.kind == "window":
            return _("Window “{t}”", t=self.window_title or self.hwnd)
        return _("Display {n}", n=self.monitor)


# ---------------------------------------------------------------------------
# Win32
# ---------------------------------------------------------------------------

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    try:
        dwmapi = ctypes.WinDLL("dwmapi")
    except OSError:  # pragma: no cover
        dwmapi = None

    HDC = wintypes.HDC
    HWND = wintypes.HWND
    HBITMAP = wintypes.HBITMAP

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD)]

    class MONITORINFOEXW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD),
                    ("szDevice", wintypes.WCHAR * 32)]

    class CURSORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                    ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]

    MONITORENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, HDC,
                                         ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM)

    def _sig(fn, res, *args):
        fn.restype = res
        fn.argtypes = list(args)

    _sig(user32.GetDC, HDC, HWND)
    _sig(user32.GetWindowDC, HDC, HWND)
    _sig(user32.ReleaseDC, ctypes.c_int, HWND, HDC)
    _sig(user32.EnumDisplayMonitors, wintypes.BOOL, HDC, ctypes.c_void_p, MONITORENUMPROC,
         wintypes.LPARAM)
    _sig(user32.GetMonitorInfoW, wintypes.BOOL, wintypes.HMONITOR, ctypes.POINTER(MONITORINFOEXW))
    _sig(user32.GetSystemMetrics, ctypes.c_int, ctypes.c_int)
    _sig(user32.EnumWindows, wintypes.BOOL, WNDENUMPROC, wintypes.LPARAM)
    _sig(user32.IsWindow, wintypes.BOOL, HWND)
    _sig(user32.IsWindowVisible, wintypes.BOOL, HWND)
    _sig(user32.IsIconic, wintypes.BOOL, HWND)
    _sig(user32.GetWindowTextLengthW, ctypes.c_int, HWND)
    _sig(user32.GetWindowTextW, ctypes.c_int, HWND, wintypes.LPWSTR, ctypes.c_int)
    _sig(user32.GetWindowLongW, wintypes.LONG, HWND, ctypes.c_int)
    _sig(user32.GetWindow, HWND, HWND, wintypes.UINT)
    _sig(user32.GetWindowRect, wintypes.BOOL, HWND, ctypes.POINTER(wintypes.RECT))
    _sig(user32.GetClientRect, wintypes.BOOL, HWND, ctypes.POINTER(wintypes.RECT))
    _sig(user32.ClientToScreen, wintypes.BOOL, HWND, ctypes.POINTER(wintypes.POINT))
    _sig(user32.GetWindowThreadProcessId, wintypes.DWORD, HWND, ctypes.POINTER(wintypes.DWORD))
    _sig(user32.GetCursorInfo, wintypes.BOOL, ctypes.POINTER(CURSORINFO))
    _sig(user32.PrintWindow, wintypes.BOOL, HWND, HDC, wintypes.UINT)
    _sig(user32.GetClassNameW, ctypes.c_int, HWND, wintypes.LPWSTR, ctypes.c_int)
    _sig(gdi32.CreateCompatibleDC, HDC, HDC)
    _sig(gdi32.CreateCompatibleBitmap, HBITMAP, HDC, ctypes.c_int, ctypes.c_int)
    _sig(gdi32.SelectObject, wintypes.HGDIOBJ, HDC, wintypes.HGDIOBJ)
    _sig(gdi32.DeleteObject, wintypes.BOOL, wintypes.HGDIOBJ)
    _sig(gdi32.DeleteDC, wintypes.BOOL, HDC)
    _sig(gdi32.BitBlt, wintypes.BOOL, HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
         HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
    _sig(gdi32.StretchBlt, wintypes.BOOL, HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
         ctypes.c_int, HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD)
    _sig(gdi32.SetStretchBltMode, ctypes.c_int, HDC, ctypes.c_int)
    _sig(gdi32.SetBrushOrgEx, wintypes.BOOL, HDC, ctypes.c_int, ctypes.c_int, ctypes.c_void_p)
    _sig(gdi32.GetDIBits, ctypes.c_int, HDC, HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
         ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT)
    _sig(kernel32.OpenProcess, wintypes.HANDLE, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _sig(kernel32.CloseHandle, wintypes.BOOL, wintypes.HANDLE)
    _sig(kernel32.QueryFullProcessImageNameW, wintypes.BOOL, wintypes.HANDLE, wintypes.DWORD,
         wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD))
    if dwmapi is not None:
        _sig(dwmapi.DwmGetWindowAttribute, ctypes.c_long, HWND, wintypes.DWORD, ctypes.c_void_p,
             wintypes.DWORD)

SRCCOPY = 0x00CC0020
HALFTONE = 4
COLORONCOLOR = 3
DIB_RGB_COLORS = 0
PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
GW_OWNER = 4
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
CURSOR_SHOWING = 0x1
MONITORINFOF_PRIMARY = 0x1
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def ensure_dpi_awareness() -> None:
    """Physical pixels regardless of display scaling (when running without Qt)."""
    if not IS_WINDOWS:
        return
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except (AttributeError, OSError):
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except (AttributeError, OSError):
        pass
    try:
        user32.SetProcessDPIAware()
    except (AttributeError, OSError):
        pass


def _rect(r: "wintypes.RECT") -> Rect:
    return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)


def virtual_screen() -> Rect:
    g = user32.GetSystemMetrics
    return Rect(g(SM_XVIRTUALSCREEN), g(SM_YVIRTUALSCREEN), g(SM_CXVIRTUALSCREEN),
                g(SM_CYVIRTUALSCREEN))


def list_monitors() -> List[MonitorInfo]:
    found: List[MonitorInfo] = []

    def cb(hmon, hdc, lprect, lparam):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            found.append(MonitorInfo(0, _rect(info.rcMonitor),
                                     bool(info.dwFlags & MONITORINFOF_PRIMARY), info.szDevice))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    # primary first, the rest left to right
    found.sort(key=lambda m: (not m.primary, m.rect.left, m.rect.top))
    for i, m in enumerate(found, 1):
        m.index = i
    return found


def _window_text(hwnd) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _is_cloaked(hwnd) -> bool:
    if dwmapi is None:
        return False
    val = wintypes.DWORD()
    if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val)) != 0:
        return False
    return val.value != 0


def process_name(hwnd) -> Tuple[int, str]:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name = ""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if h:
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                name = os.path.basename(buf.value)
        finally:
            kernel32.CloseHandle(h)
    return pid.value, name


def window_frame_rect(hwnd) -> Optional[Rect]:
    """Visible window bounds (from DWM, without the invisible resize border)."""
    if dwmapi is not None:
        r = wintypes.RECT()
        if dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r),
                                        ctypes.sizeof(r)) == 0:
            rect = _rect(r)
            if rect.valid:
                return rect
    r = wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return _rect(r)
    return None


def window_client_rect(hwnd) -> Optional[Rect]:
    r = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(r)):
        return None
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return Rect(pt.x, pt.y, r.right - r.left, r.bottom - r.top)


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def list_windows() -> List[WindowInfo]:
    own_pid = os.getpid()
    out: List[WindowInfo] = []
    skip_classes = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}

    def cb(hwnd, lparam):
        try:
            if not user32.IsWindowVisible(hwnd) or _is_cloaked(hwnd):
                return True
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if ex & WS_EX_TOOLWINDOW:
                return True
            if user32.GetWindow(hwnd, GW_OWNER) and not (ex & 0x00040000):  # WS_EX_APPWINDOW
                return True
            title = _window_text(hwnd)
            if not title or _class_name(hwnd) in skip_classes:
                return True
            pid, proc = process_name(hwnd)
            if pid == own_pid:
                return True
            minimized = bool(user32.IsIconic(hwnd))
            rect = window_frame_rect(hwnd) or Rect(0, 0, 0, 0)
            if not minimized and (rect.width < 40 or rect.height < 40):
                return True
            out.append(WindowInfo(int(hwnd), title, proc or "?", rect, minimized))
        except Exception:
            pass
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def find_window(title: str, process: str) -> Optional[WindowInfo]:
    """Find a window again (after the program was restarted)."""
    wins = list_windows()
    for w in wins:
        if w.title == title and (not process or w.process == process):
            return w
    if process:
        for w in wins:
            if w.process == process:
                return w
    return None


def cursor_position() -> Optional[Tuple[int, int]]:
    """Mouse pointer on screen (None when hidden)."""
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(CURSORINFO)
    if not user32.GetCursorInfo(ctypes.byref(ci)) or not (ci.flags & CURSOR_SHOWING):
        return None
    return ci.ptScreenPos.x, ci.ptScreenPos.y


def capture_scale(width: int, height: int, target: int = 160, oversample: float = 2.0) -> float:
    """How much to shrink while capturing: keep ~2x the final size."""
    if width <= 0 or height <= 0:
        return 1.0
    return min(1.0, (target * oversample) / min(width, height))


# ---------------------------------------------------------------------------
# Grabber - create and use it on the same thread
# ---------------------------------------------------------------------------

class _Surface:
    """Memory DC + bitmap, re-created when the size changes."""

    def __init__(self, ref_dc) -> None:
        self.dc = gdi32.CreateCompatibleDC(ref_dc)
        if not self.dc:
            raise CaptureError("CreateCompatibleDC failed")
        self._ref = ref_dc
        self.bmp = None
        self._old = None
        self.size = (0, 0)

    def ensure(self, w: int, h: int) -> None:
        if (w, h) == self.size:
            return
        self._free_bitmap()
        bmp = gdi32.CreateCompatibleBitmap(self._ref, w, h)
        if not bmp:
            raise CaptureError(f"CreateCompatibleBitmap {w}x{h} failed")
        self._old = gdi32.SelectObject(self.dc, bmp)
        self.bmp = bmp
        self.size = (w, h)

    def read(self) -> Image.Image:
        w, h = self.size
        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth, bi.biHeight = w, -h          # negative = top-down
        bi.biPlanes, bi.biBitCount = 1, 32
        buf = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(self.dc, self.bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS) != h:
            raise CaptureError("GetDIBits failed")
        return Image.frombuffer("RGB", (w, h), buf.raw, "raw", "BGRX", 0, 1)

    def _free_bitmap(self) -> None:
        if self.bmp:
            if self._old:
                gdi32.SelectObject(self.dc, self._old)
            gdi32.DeleteObject(self.bmp)
        self.bmp = None
        self._old = None
        self.size = (0, 0)

    def close(self) -> None:
        self._free_bitmap()
        if self.dc:
            gdi32.DeleteDC(self.dc)
            self.dc = None


class ScreenGrabber:
    def __init__(self, target: int = 160, oversample: float = 2.0) -> None:
        if not IS_WINDOWS:
            raise CaptureError("Screen capture only works on Windows")
        self.target = target
        self.oversample = oversample
        self._screen_dc = user32.GetDC(None)
        if not self._screen_dc:
            raise CaptureError("Could not get the screen DC")
        self._out = _Surface(self._screen_dc)
        self._full: Optional[_Surface] = None
        self._monitors: Optional[List[MonitorInfo]] = None

    def close(self) -> None:
        self._out.close()
        if self._full is not None:
            self._full.close()
        if self._screen_dc:
            user32.ReleaseDC(None, self._screen_dc)
            self._screen_dc = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def refresh_monitors(self) -> None:
        self._monitors = None

    def source_rect(self, src: CaptureSource) -> Rect:
        """Source rectangle on screen (physical pixels)."""
        if src.kind == "region":
            if not src.region or not src.region.valid:
                raise CaptureError(_("No region selected."))
            return src.region.intersect(virtual_screen())
        if src.kind == "window":
            if not is_window(src.hwnd):
                raise WindowGone(_("The window was closed."))
            if user32.IsIconic(src.hwnd):
                raise SourceUnavailable(_("The window is minimized — restore it."))
            r = window_client_rect(src.hwnd) if src.client_only else window_frame_rect(src.hwnd)
            if r is None or not r.valid:
                raise SourceUnavailable(_("The window size could not be read."))
            return r
        if self._monitors is None:
            self._monitors = list_monitors()
        mons = self._monitors
        if not mons:
            return virtual_screen()
        idx = min(max(1, src.monitor), len(mons))
        return mons[idx - 1].rect

    def grab(self, src: CaptureSource, with_cursor: bool = True
             ) -> Tuple[Image.Image, Optional[Tuple[float, float]], Rect]:
        """Returns (reduced image, cursor position in that image or None, source rect)."""
        if src.kind == "window" and src.method == "printwindow":
            return self._grab_printwindow(src, with_cursor)
        rect = self.source_rect(src)
        visible = rect.intersect(virtual_screen())
        if not visible.valid:
            raise SourceUnavailable(_("The region is outside the screen."))
        s = capture_scale(visible.width, visible.height, self.target, self.oversample)
        ow, oh = max(1, round(visible.width * s)), max(1, round(visible.height * s))
        self._out.ensure(ow, oh)
        if (ow, oh) == (visible.width, visible.height):
            ok = gdi32.BitBlt(self._out.dc, 0, 0, ow, oh, self._screen_dc,
                              visible.left, visible.top, SRCCOPY)
        else:
            gdi32.SetStretchBltMode(self._out.dc, HALFTONE)
            gdi32.SetBrushOrgEx(self._out.dc, 0, 0, None)
            ok = gdi32.StretchBlt(self._out.dc, 0, 0, ow, oh, self._screen_dc, visible.left,
                                  visible.top, visible.width, visible.height, SRCCOPY)
        if not ok:
            raise CaptureError(_("Screen copy failed (the screen may be locked)."))
        img = self._out.read()
        cur = None
        if with_cursor:
            p = cursor_position()
            if p and visible.left <= p[0] < visible.right and visible.top <= p[1] < visible.bottom:
                cur = ((p[0] - visible.left) * ow / visible.width,
                       (p[1] - visible.top) * oh / visible.height)
        return img, cur, visible

    def _grab_printwindow(self, src: CaptureSource, with_cursor: bool):
        hwnd = src.hwnd
        if not is_window(hwnd):
            raise WindowGone(_("The window was closed."))
        if user32.IsIconic(hwnd):
            raise SourceUnavailable(_("The window is minimized — restore it."))
        wr = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(wr))
        win = _rect(wr)
        if src.client_only:
            target = window_client_rect(hwnd)
            flags = PW_CLIENTONLY | PW_RENDERFULLCONTENT
            base = target
        else:
            target = window_frame_rect(hwnd)
            flags = PW_RENDERFULLCONTENT
            base = win                     # PrintWindow draws the whole window (incl. invisible border)
        if target is None or not target.valid or not base.valid:
            raise SourceUnavailable(_("The window size could not be read."))
        if self._full is None:
            self._full = _Surface(self._screen_dc)
        self._full.ensure(base.width, base.height)
        if not user32.PrintWindow(hwnd, self._full.dc, flags):
            raise CaptureError(_("PrintWindow failed — try the “From screen” capture method."))
        # visible part inside base
        ox, oy = target.left - base.left, target.top - base.top
        s = capture_scale(target.width, target.height, self.target, self.oversample)
        ow, oh = max(1, round(target.width * s)), max(1, round(target.height * s))
        self._out.ensure(ow, oh)
        gdi32.SetStretchBltMode(self._out.dc, HALFTONE)
        gdi32.SetBrushOrgEx(self._out.dc, 0, 0, None)
        gdi32.StretchBlt(self._out.dc, 0, 0, ow, oh, self._full.dc, ox, oy, target.width,
                         target.height, SRCCOPY)
        img = self._out.read()
        cur = None
        if with_cursor:
            p = cursor_position()
            if p and target.left <= p[0] < target.right and target.top <= p[1] < target.bottom:
                cur = ((p[0] - target.left) * ow / target.width,
                       (p[1] - target.top) * oh / target.height)
        return img, cur, target

    def grab_full(self, rect: Optional[Rect] = None, max_side: int = 0) -> Image.Image:
        """Full (or reduced to max_side) screenshot - for the region picker."""
        rect = rect or virtual_screen()
        s = min(1.0, max_side / max(rect.width, rect.height)) if max_side else 1.0
        ow, oh = max(1, round(rect.width * s)), max(1, round(rect.height * s))
        surf = _Surface(self._screen_dc)
        try:
            surf.ensure(ow, oh)
            gdi32.SetStretchBltMode(surf.dc, HALFTONE)
            gdi32.SetBrushOrgEx(surf.dc, 0, 0, None)
            gdi32.StretchBlt(surf.dc, 0, 0, ow, oh, self._screen_dc, rect.left, rect.top,
                             rect.width, rect.height, SRCCOPY)
            return surf.read()
        finally:
            surf.close()
