"""System stats on the MiniToo: CPU, graphics card, RAM and network load,
temperatures and the time - as a 160x128 dashboard.

Sensors (all work without administrator rights):

* Windows performance counters (PDH): CPU load, graphics card load (the
  busiest GPU engine, like Task Manager), network throughput and the ACPI
  thermal zone temperature (reported in Kelvin);
* GlobalMemoryStatusEx: RAM;
* optional, when present: ``nvidia-smi`` (NVIDIA cards: GPU temperature) and
  the LibreHardwareMonitor web server (``http://127.0.0.1:8085/data.json``:
  exact CPU package and GPU temperatures).

A sensor that is missing shows "—" instead of a number. Nothing here talks
to the MiniToo - the dashboard is an ordinary picture for MirrorEngine.
"""

from __future__ import annotations

import collections
import ctypes
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw

from .capture import Rect, SourceUnavailable
from .i18n import _
from .imaging import get_font

log = logging.getLogger("minitoo.stats")

IS_WINDOWS = sys.platform == "win32"
LHM_URL = "http://127.0.0.1:8085/data.json"
HISTORY = 60                                # seconds of history for the small graphs
SIZE = (160, 128)


@dataclass
class Reading:
    t: float = 0.0                          # time.time()
    cpu: Optional[float] = None             # %
    cpu_temp: Optional[float] = None        # °C
    gpu: Optional[float] = None             # %
    gpu_temp: Optional[float] = None        # °C
    ram_used: Optional[int] = None          # bytes
    ram_total: Optional[int] = None         # bytes
    net_down: Optional[float] = None        # bytes per second
    net_up: Optional[float] = None
    sources: Tuple[str, ...] = ()

    @property
    def ram(self) -> Optional[float]:
        if not self.ram_total or self.ram_used is None:
            return None
        return 100.0 * self.ram_used / self.ram_total


# ---------------------------------------------------------------------------
# Windows performance counters (PDH)
# ---------------------------------------------------------------------------

PDH_FMT_DOUBLE = 0x00000200
PDH_FMT_NOCAP100 = 0x00008000
PDH_MORE_DATA = 0x800007D2
PDH_VALID = (0, 1)                          # PDH_CSTATUS_VALID_DATA, PDH_CSTATUS_NEW_DATA


class _FmtUnion(ctypes.Union):
    _fields_ = [("longValue", ctypes.c_long), ("doubleValue", ctypes.c_double),
                ("largeValue", ctypes.c_longlong), ("strValue", ctypes.c_void_p)]


class _FmtValue(ctypes.Structure):
    _fields_ = [("CStatus", ctypes.c_uint32), ("u", _FmtUnion)]


class _FmtItem(ctypes.Structure):
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _FmtValue)]


class PdhQuery:
    """A small PDH wrapper: add English counter paths, collect, read."""

    def __init__(self) -> None:
        if not IS_WINDOWS:
            raise OSError("PDH needs Windows")
        pdh = ctypes.WinDLL("pdh")
        self._pdh = pdh
        u32, vp = ctypes.c_uint32, ctypes.c_void_p
        pdh.PdhOpenQueryW.restype = u32
        pdh.PdhOpenQueryW.argtypes = [ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(vp)]
        pdh.PdhAddEnglishCounterW.restype = u32
        pdh.PdhAddEnglishCounterW.argtypes = [vp, ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(vp)]
        pdh.PdhCollectQueryData.restype = u32
        pdh.PdhCollectQueryData.argtypes = [vp]
        pdh.PdhGetFormattedCounterValue.restype = u32
        pdh.PdhGetFormattedCounterValue.argtypes = [vp, u32, ctypes.POINTER(u32), ctypes.POINTER(_FmtValue)]
        pdh.PdhGetFormattedCounterArrayW.restype = u32
        pdh.PdhGetFormattedCounterArrayW.argtypes = [vp, u32, ctypes.POINTER(u32), ctypes.POINTER(u32), vp]
        pdh.PdhCloseQuery.restype = u32
        pdh.PdhCloseQuery.argtypes = [vp]
        self._q = vp()
        st = pdh.PdhOpenQueryW(None, 0, ctypes.byref(self._q))
        if st:
            raise OSError(f"PdhOpenQuery 0x{st:08X}")
        self._counters: Dict[str, ctypes.c_void_p] = {}

    def add(self, key: str, path: str) -> bool:
        h = ctypes.c_void_p()
        st = self._pdh.PdhAddEnglishCounterW(self._q, path, 0, ctypes.byref(h))
        if st:
            log.debug("PDH counter %s: 0x%08X", path, st)
            return False
        self._counters[key] = h
        return True

    def has(self, key: str) -> bool:
        return key in self._counters

    def collect(self) -> None:
        if self._q:
            self._pdh.PdhCollectQueryData(self._q)

    def value(self, key: str) -> Optional[float]:
        h = self._counters.get(key)
        if h is None:
            return None
        v = _FmtValue()
        st = self._pdh.PdhGetFormattedCounterValue(h, PDH_FMT_DOUBLE | PDH_FMT_NOCAP100, None,
                                                   ctypes.byref(v))
        if st or v.CStatus not in PDH_VALID:
            return None
        return v.u.doubleValue

    def array(self, key: str) -> Optional[List[Tuple[str, float]]]:
        """All instances of a wildcard counter, or None if it cannot be read."""
        h = self._counters.get(key)
        if h is None:
            return None
        fmt = PDH_FMT_DOUBLE | PDH_FMT_NOCAP100
        for _attempt in range(3):          # instances may appear between the two calls
            size, count = ctypes.c_uint32(0), ctypes.c_uint32(0)
            st = self._pdh.PdhGetFormattedCounterArrayW(h, fmt, ctypes.byref(size),
                                                        ctypes.byref(count), None)
            if st == 0:
                return []
            if st != PDH_MORE_DATA:
                return None
            buf = ctypes.create_string_buffer(size.value + 1024)
            size.value = len(buf)
            st = self._pdh.PdhGetFormattedCounterArrayW(h, fmt, ctypes.byref(size),
                                                        ctypes.byref(count), buf)
            if st == PDH_MORE_DATA:
                continue
            if st:
                return None
            items = ctypes.cast(buf, ctypes.POINTER(_FmtItem))
            return [(items[i].szName or "", items[i].FmtValue.u.doubleValue)
                    for i in range(count.value) if items[i].FmtValue.CStatus in PDH_VALID]
        return None

    def close(self) -> None:
        if self._q:
            self._pdh.PdhCloseQuery(self._q)
            self._q = ctypes.c_void_p()


def gpu_load_from_engines(items: List[Tuple[str, float]]) -> float:
    """GPU load like Task Manager: per engine, add up all processes; the
    busiest engine is the GPU load. Instance names look like
    ``pid_1234_luid_0x0_0xC9F1_phys_0_eng_0_engtype_3D``."""
    per_engine: Dict[str, float] = collections.defaultdict(float)
    for name, value in items:
        per_engine[re.sub(r"^pid_\d+_", "", name)] += max(0.0, value)
    return min(100.0, max(per_engine.values(), default=0.0))


def thermal_zone_celsius(items: List[Tuple[str, float]]) -> Optional[float]:
    """The hottest ACPI thermal zone (PDH reports Kelvin)."""
    temps = [k - 273.15 for _n, k in items if 0 < k - 273.15 < 125]
    return max(temps) if temps else None


_TUNNELS = ("isatap", "teredo", "6to4", "loopback")


def net_total(items: List[Tuple[str, float]]) -> float:
    return sum(max(0.0, v) for n, v in items if not any(t in n.lower() for t in _TUNNELS))


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64)]


def memory() -> Optional[Tuple[int, int]]:
    """(used, total) RAM in bytes."""
    if not IS_WINDOWS:
        return None
    m = _MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        return None
    return int(m.ullTotalPhys - m.ullAvailPhys), int(m.ullTotalPhys)


# ---------------------------------------------------------------------------
# Optional sensors: nvidia-smi and LibreHardwareMonitor
# ---------------------------------------------------------------------------

def find_nvidia_smi() -> Optional[str]:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    for p in (os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "nvidia-smi.exe"),
              r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
        if os.path.isfile(p):
            return p
    return None


def parse_nvidia_smi(text: str) -> Optional[Tuple[float, float]]:
    """First line of ``--query-gpu=temperature.gpu,utilization.gpu
    --format=csv,noheader,nounits`` -> (temperature °C, load %)."""
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            try:
                return float(parts[0]), float(parts[1])
            except ValueError:
                return None
    return None


def _lhm_number(value) -> Optional[float]:
    m = re.match(r"\s*(-?\d+(?:[.,]\d+)?)", str(value or ""))
    return float(m.group(1).replace(",", ".")) if m else None


def parse_lhm(data: dict) -> Dict[str, float]:
    """LibreHardwareMonitor ``data.json`` -> {"cpu_temp", "gpu_temp", "gpu"}
    (only the keys that were found). Uses the sensor id when the version has
    one (``/amdcpu/0/temperature/2``), otherwise the hardware icon and the
    group name."""
    found: Dict[str, List[Tuple[int, float]]] = collections.defaultdict(list)

    def hw_kind(node) -> str:
        icon = str(node.get("ImageURL", "")).lower()
        if "cpu" in icon:
            return "cpu"
        if any(k in icon for k in ("nvidia", "ati", "amd", "gpu")):
            return "gpu"
        return ""

    def add(kind: str, group: str, text: str, value: Optional[float]) -> None:
        if value is None or kind not in ("cpu", "gpu"):
            return
        t = text.lower()
        if group == "temperature":
            rank = 0 if ("package" in t or "tctl" in t or "tdie" in t or t == "gpu core") else 1
            if "distance" in t or "hot spot" in t or "limit" in t:
                return
            found[kind + "_temp"].append((rank, value))
        elif group == "load" and kind == "gpu" and t == "gpu core":
            found["gpu"].append((0, value))

    def walk(node, kind: str, group: str) -> None:
        sid = str(node.get("SensorId", "") or "")
        text = str(node.get("Text", ""))
        if sid:
            m = re.match(r"/([a-z\-]+)/\d+/([a-z]+)/", sid)
            if m:
                hw, grp = m.group(1), m.group(2)
                k = "cpu" if hw.endswith("cpu") else "gpu" if hw.startswith("gpu") else ""
                add(k, grp, text, _lhm_number(node.get("Value")))
                return
        kind = hw_kind(node) or kind
        lowered = text.lower()
        if lowered in ("temperatures", "load"):
            group = "temperature" if lowered == "temperatures" else "load"
        children = node.get("Children") or []
        if not children and group and "Value" in node:
            add(kind, group, text, _lhm_number(node.get("Value")))
        for c in children:
            if isinstance(c, dict):
                walk(c, kind, group)

    if isinstance(data, dict):
        walk(data, "", "")
    out: Dict[str, float] = {}
    for key, values in found.items():
        best = min(r for r, _v in values)
        out[key] = max(v for r, v in values if r == best)
    return out


# ---------------------------------------------------------------------------
# Sampler
# ---------------------------------------------------------------------------

class SystemSampler:
    """Reads every sensor once per call to sample(). Not thread-safe: use it
    from one thread (StatsMonitor does)."""

    def __init__(self, lhm_url: str = LHM_URL, use_nvidia: bool = True) -> None:
        self._q: Optional[PdhQuery] = None
        if IS_WINDOWS:
            try:
                q = PdhQuery()
                q.add("cpu", r"\Processor(_Total)\% Processor Time")
                q.add("thermal", r"\Thermal Zone Information(*)\Temperature")
                q.add("gpu", r"\GPU Engine(*)\Utilization Percentage")
                q.add("down", r"\Network Interface(*)\Bytes Received/sec")
                q.add("up", r"\Network Interface(*)\Bytes Sent/sec")
                q.collect()                # rates need two samples
                self._q = q
            except OSError as e:
                log.warning("performance counters: %s", e)
        self._nvsmi = find_nvidia_smi() if use_nvidia else None
        self._nv_next = 0.0
        self._nv: Optional[Tuple[float, float]] = None
        self._nv_fails = 0
        self._lhm_url = lhm_url
        self._lhm_next = 0.0
        self._lhm: Dict[str, float] = {}
        self._jobs: Dict[str, threading.Thread] = {}

    def _kick(self, name: str, fn: Callable[[], None]) -> None:
        """Slow optional sensors run on their own short-lived thread, so a
        missing program or server never delays the once-per-second update."""
        th = self._jobs.get(name)
        if th is None or not th.is_alive():
            th = threading.Thread(target=fn, name="minitoo-stats-" + name, daemon=True)
            self._jobs[name] = th
            th.start()

    def _nvidia_job(self) -> None:
        exe = self._nvsmi
        if not exe:
            return
        try:
            out = subprocess.run([exe, "--query-gpu=temperature.gpu,utilization.gpu",
                                  "--format=csv,noheader,nounits"], capture_output=True, text=True,
                                 timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            nv = parse_nvidia_smi(out.stdout) if out.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            nv = None
        self._nv = nv
        if nv is None:
            self._nv_fails += 1
            if self._nv_fails >= 3:
                self._nvsmi = None         # not an NVIDIA system after all
        else:
            self._nv_fails = 0

    def _lhm_job(self) -> None:
        try:
            with urllib.request.urlopen(self._lhm_url, timeout=2) as r:
                self._lhm = parse_lhm(json.loads(r.read(4_000_000).decode("utf-8", "replace")))
            self._lhm_next = time.monotonic() + 2.0
        except (OSError, ValueError, urllib.error.URLError):
            self._lhm = {}
            self._lhm_next = time.monotonic() + 20.0    # not running - ask again later

    def _nvidia(self, now: float) -> Optional[Tuple[float, float]]:
        if self._nvsmi and now >= self._nv_next:
            self._nv_next = now + 3.0
            self._kick("nvidia", self._nvidia_job)
        return self._nv if self._nvsmi else None

    def _lhm_read(self, now: float) -> Dict[str, float]:
        if self._lhm_url and now >= self._lhm_next:
            self._lhm_next = now + 30.0    # the job sets the real time
            self._kick("lhm", self._lhm_job)
        return self._lhm

    def sample(self) -> Reading:
        now = time.monotonic()
        r = Reading(t=time.time())
        src: List[str] = []
        q = self._q
        if q is not None:
            q.collect()
            src.append("PDH")
            r.cpu = q.value("cpu")
            if r.cpu is not None:
                r.cpu = max(0.0, min(100.0, r.cpu))
            thermal = q.array("thermal")
            if thermal:
                r.cpu_temp = thermal_zone_celsius(thermal)
                if r.cpu_temp is not None:
                    src.append("ACPI")
            engines = q.array("gpu")
            if engines is not None:
                r.gpu = gpu_load_from_engines(engines)
            down, up = q.array("down"), q.array("up")
            if down is not None and up is not None:
                r.net_down, r.net_up = net_total(down), net_total(up)
        mem = memory()
        if mem:
            r.ram_used, r.ram_total = mem
        nv = self._nvidia(now)
        if nv is not None:
            r.gpu_temp = nv[0]
            if r.gpu is None:
                r.gpu = nv[1]
            src.append("nvidia-smi")
        lhm = self._lhm_read(now)
        if lhm:
            src.append("LibreHardwareMonitor")
            r.cpu_temp = lhm.get("cpu_temp", r.cpu_temp)
            r.gpu_temp = lhm.get("gpu_temp", r.gpu_temp)
            if r.gpu is None:
                r.gpu = lhm.get("gpu")
        r.sources = tuple(src)
        return r

    def close(self) -> None:
        if self._q is not None:
            self._q.close()
            self._q = None


# ---------------------------------------------------------------------------
# Dashboard picture
# ---------------------------------------------------------------------------

BG = (6, 9, 14)
TILE = (20, 26, 36)
TEXT = (242, 245, 250)
DIM = (135, 146, 162)
WARN = (255, 183, 77)
HOT = (255, 82, 82)
ACCENT = {"cpu": (70, 190, 255), "gpu": (186, 140, 255), "ram": (80, 220, 130),
          "net": (255, 183, 77)}


def _mix(a, b, t: float):
    return tuple(int(round(x + (y - x) * t)) for x, y in zip(a, b))


def temp_color(c: Optional[float]):
    if c is None:
        return DIM
    return HOT if c >= 85 else WARN if c >= 70 else DIM


def format_rate(bps: Optional[float]) -> Tuple[str, str]:
    """Bytes per second -> ("1.2", "MB/s")."""
    if bps is None:
        return "—", ""
    for unit, div in (("GB/s", 1024 ** 3), ("MB/s", 1024 ** 2), ("KB/s", 1024)):
        if bps >= div:
            v = bps / div
            return (f"{v:.1f}" if v < 10 else f"{v:.0f}"), unit
    return f"{bps:.0f}", "B/s"


def _short_rate(bps: Optional[float]) -> str:
    num, unit = format_rate(bps)
    return num + (unit[:1] if unit and unit != "B/s" else "")


def _gb(n: Optional[int]) -> str:
    if n is None:
        return "—"
    v = n / 1024 ** 3
    return f"{v:.1f}" if v < 100 else f"{v:.0f}"


def _graph(d: ImageDraw.ImageDraw, box, values: List[float], color, top: float) -> None:
    x0, y0, x1, y1 = box
    if len(values) < 2 or top <= 0:
        return
    n = HISTORY
    vals = values[-n:]
    step = (x1 - x0) / (n - 1)
    start = x1 - step * (len(vals) - 1)
    pts = [(start + i * step, y1 - (y1 - y0) * max(0.0, min(1.0, v / top))) for i, v in enumerate(vals)]
    d.polygon([(pts[0][0], y1)] + pts + [(pts[-1][0], y1)], fill=_mix(TILE, color, 0.22))
    d.line(pts, fill=_mix(TILE, color, 0.55), width=1)


def _big(d: ImageDraw.ImageDraw, x: int, base: int, number: str, unit: str, color=TEXT,
         max_w: int = 66) -> None:
    """A big number with a small unit; smaller font if it would not fit."""
    small = get_font(11)
    unit_w = d.textlength(unit, font=small) + 1 if unit else 0
    for size in (22, 19, 16, 14):
        big = get_font(size)
        w = d.textlength(number, font=big)
        if w + unit_w <= max_w:
            break
    d.text((x, base), number, font=big, fill=color, anchor="ls")
    if unit:
        d.text((x + w + 1, base), unit, font=small, fill=DIM, anchor="ls")


def _arrow(d: ImageDraw.ImageDraw, x: int, y: int, up: bool, color) -> None:
    if up:
        d.polygon([(x, y + 5), (x + 6, y + 5), (x + 3, y)], fill=color)
    else:
        d.polygon([(x, y), (x + 6, y), (x + 3, y + 5)], fill=color)


def _tile(d: ImageDraw.ImageDraw, box, key: str, label: str) -> Tuple[int, int, int, int]:
    x0, y0, x1, y1 = box
    d.rounded_rectangle(box, radius=4, fill=TILE)
    d.text((x0 + 5, y0 + 12), label, font=get_font(10), fill=ACCENT[key], anchor="ls")
    return box


def _bar(d: ImageDraw.ImageDraw, box, pct: Optional[float], color) -> None:
    x0, y0, x1, y1 = box
    bx0, bx1, by = x0 + 5, x1 - 5, y1 - 5
    d.rectangle([bx0, by, bx1, by + 2], fill=_mix(TILE, BG, 0.6))
    if pct is not None and pct > 0:
        w = int(round((bx1 - bx0) * min(100.0, pct) / 100))
        if w > 0:
            d.rectangle([bx0, by, bx0 + w, by + 2], fill=HOT if pct >= 90 else color)


def _pct(v: Optional[float]) -> Tuple[str, str]:
    return ("—", "") if v is None else (f"{v:.0f}", "%")


def render_dashboard(r: Optional[Reading], history: Optional[Dict[str, List[float]]] = None,
                     now: Optional[float] = None) -> Image.Image:
    """The 160x128 dashboard picture: the time on top, then four tiles
    (CPU, GPU, RAM, network) with small graphs of the last minute."""
    r = r or Reading()
    history = history or {}
    w, h = SIZE
    img = Image.new("RGB", SIZE, BG)
    d = ImageDraw.Draw(img)
    lt = time.localtime(now if now is not None else (r.t or time.time()))
    d.text((4, 11), time.strftime("%H:%M", lt), font=get_font(11), fill=TEXT, anchor="ls")
    d.text((w - 4, 11), time.strftime("%d.%m", lt), font=get_font(10, False), fill=DIM, anchor="rs")

    top, gap, margin = 14, 2, 2
    tw = (w - 2 * margin - gap) // 2
    th = (h - top - margin - gap) // 2
    boxes = {}
    for i, key in enumerate(("cpu", "gpu", "ram", "net")):
        cx = margin + (i % 2) * (tw + gap)
        cy = top + (i // 2) * (th + gap)
        boxes[key] = (cx, cy, cx + tw - 1, cy + th - 1)

    def right_info(box, text: str, color) -> None:
        d.text((box[2] - 5, box[1] + 12), text, font=get_font(10), fill=color, anchor="rs")

    # CPU
    b = _tile(d, boxes["cpu"], "cpu", "CPU")
    _graph(d, (b[0] + 1, b[1] + 18, b[2] - 1, b[3] - 8), history.get("cpu", []), ACCENT["cpu"], 100)
    if r.cpu_temp is not None:
        right_info(b, f"{r.cpu_temp:.0f}°C", temp_color(r.cpu_temp))
    _big(d, b[0] + 5, b[1] + 38, *_pct(r.cpu))
    _bar(d, b, r.cpu, ACCENT["cpu"])
    # GPU
    b = _tile(d, boxes["gpu"], "gpu", "GPU")
    _graph(d, (b[0] + 1, b[1] + 18, b[2] - 1, b[3] - 8), history.get("gpu", []), ACCENT["gpu"], 100)
    if r.gpu_temp is not None:
        right_info(b, f"{r.gpu_temp:.0f}°C", temp_color(r.gpu_temp))
    _big(d, b[0] + 5, b[1] + 38, *_pct(r.gpu))
    _bar(d, b, r.gpu, ACCENT["gpu"])
    # RAM
    b = _tile(d, boxes["ram"], "ram", "RAM")
    _big(d, b[0] + 5, b[1] + 34, *_pct(r.ram))
    d.text((b[0] + 5, b[3] - 9), f"{_gb(r.ram_used)} / {_gb(r.ram_total)} GB",
           font=get_font(10, False), fill=DIM, anchor="ls")
    _bar(d, b, r.ram, ACCENT["ram"])
    # network
    b = _tile(d, boxes["net"], "net", "NET")
    down_hist = history.get("down", [])
    _graph(d, (b[0] + 1, b[1] + 18, b[2] - 1, b[3] - 3), down_hist, ACCENT["net"],
           max([64 * 1024.0] + down_hist))
    up_text = _short_rate(r.net_up)
    f10 = get_font(10)
    uw = d.textlength(up_text, font=f10)
    right_info(b, up_text, DIM)
    _arrow(d, int(b[2] - 5 - uw - 9), b[1] + 4, True, DIM)
    _arrow(d, b[0] + 5, b[1] + 26, False, ACCENT["net"])
    num, unit = format_rate(r.net_down)
    _big(d, b[0] + 14, b[1] + 38, num, unit, max_w=b[2] - b[0] - 18)
    return img


# ---------------------------------------------------------------------------
# Background monitor + grabber for MirrorEngine
# ---------------------------------------------------------------------------

class StatsMonitor:
    """Samples the sensors once per second on its own thread and keeps the
    newest dashboard picture ready."""

    def __init__(self, interval: float = 1.0,
                 sampler_factory: Optional[Callable[[], SystemSampler]] = None) -> None:
        self.interval = interval
        self._factory = sampler_factory or SystemSampler
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._latest: Optional[Reading] = None
        self._image: Optional[Image.Image] = None
        self._hist: Dict[str, Deque[float]] = {k: collections.deque(maxlen=HISTORY)
                                                for k in ("cpu", "gpu", "ram", "down", "up")}

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="minitoo-stats", daemon=True)
        self._thread.start()

    def stop(self, wait: bool = False) -> None:
        self._stop.set()
        th = self._thread
        if wait and th is not None and th is not threading.current_thread():
            th.join(5.0)

    def latest(self) -> Optional[Reading]:
        with self._lock:
            return self._latest

    def image(self) -> Optional[Image.Image]:
        with self._lock:
            return self._image

    def history(self) -> Dict[str, List[float]]:
        with self._lock:
            return {k: list(v) for k, v in self._hist.items()}

    def _record(self, r: Reading) -> None:
        for key, value in (("cpu", r.cpu), ("gpu", r.gpu), ("ram", r.ram),
                           ("down", r.net_down), ("up", r.net_up)):
            if value is not None:
                self._hist[key].append(float(value))
        img = render_dashboard(r, {k: list(v) for k, v in self._hist.items()})
        with self._lock:
            self._latest = r
            self._image = img

    def _run(self) -> None:
        sampler = None
        try:
            sampler = self._factory()
            self._stop.wait(min(0.5, self.interval))   # first rates need some time
            while not self._stop.is_set():
                t0 = time.monotonic()
                try:
                    self._record(sampler.sample())
                except Exception:  # pragma: no cover - one bad sample must not stop it
                    log.exception("stats sample")
                self._stop.wait(max(0.05, self.interval - (time.monotonic() - t0)))
        except Exception:  # pragma: no cover
            log.exception("stats thread")
        finally:
            if sampler is not None:
                try:
                    sampler.close()
                except Exception:
                    pass


class StatsGrabber:
    """Grabber interface for MirrorEngine (like capture.ScreenGrabber)."""

    def __init__(self, monitor: StatsMonitor) -> None:
        self.monitor = monitor

    def grab(self, src=None, with_cursor: bool = False):
        img = self.monitor.image()
        if img is None:
            raise SourceUnavailable(_("Starting…"))
        return img, None, Rect(0, 0, img.width, img.height)

    def refresh_monitors(self) -> None:
        pass

    def close(self) -> None:
        pass                               # the monitor is shared with the preview
