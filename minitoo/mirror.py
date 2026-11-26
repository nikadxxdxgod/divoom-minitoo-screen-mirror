"""Mirroring: capture -> process -> encode -> upload, again and again.

The MiniToo has no streaming mode - every frame is a separate, complete
upload (start -> chunks -> acknowledgement). Therefore:

* capture runs on its own thread: while one frame travels over Bluetooth
  the next one is already being prepared, and the newest frame is always
  the one that gets sent;
* unchanged frames are not sent (Bluetooth and CPU rest);
* in "batch" mode (experimental) several frames go out as one animation -
  smoother motion, more latency.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Callable, Deque, List, NamedTuple, Optional, Tuple

from PIL import Image, ImageChops

from .capture import (CaptureError, CaptureSource, ScreenGrabber, SourceUnavailable, WindowGone)
from .device import MiniTooDevice, NotConnected, UploadError, UploadResult
from .encoder import EncodedFrame, FrameEncoder
from .i18n import _
from .imaging import ImageOptions, process_frame
from .protocol import FORMATS

log = logging.getLogger("minitoo.mirror")

MAX_FPS = 30.0


@dataclass
class MirrorConfig:
    source: CaptureSource = field(default_factory=CaptureSource)
    image: ImageOptions = field(default_factory=ImageOptions)
    fmt_key: str = "jpeg160"
    jpeg_quality: int = 95
    chroma_444: bool = True
    max_frame_kb: int = 0                # 0 = no limit
    zstd_level: int = 9
    target_fps: float = 15.0
    skip_unchanged: bool = True
    change_pixels: int = 6               # fewer changed pixels than this = "unchanged"
    keepalive_s: float = 0.0             # re-send an unchanged screen every N s (0 = no)
    batch_frames: int = 1                # >1 = as an animation (experimental)
    batch_interval_ms: int = 150


@dataclass
class MirrorStats:
    running: bool = False
    status: str = ""
    fps: float = 0.0                     # device updates per second
    shown_fps: float = 0.0               # frames shown per second (batch mode > fps)
    kbps: float = 0.0
    latency_ms: float = 0.0              # capture -> device confirmation
    upload_ms: float = 0.0
    capture_ms: float = 0.0
    encode_ms: float = 0.0
    frame_bytes: int = 0
    quality: int = 0
    interval_ms: float = 0.0
    uploads: int = 0
    skipped: int = 0
    errors: int = 0
    resends: int = 0
    acked: bool = False


class _Captured(NamedTuple):
    seq: int
    t: float                             # time.perf_counter() when the capture started
    img: Image.Image
    version: int                         # settings version (size / format)


def changed_pixels(a: Optional[Image.Image], b: Optional[Image.Image], tolerance: int = 24) -> int:
    """How many pixels changed noticeably (ignores JPEG noise)."""
    if a is None or b is None or a.size != b.size:
        return 1 << 30
    diff = ImageChops.difference(a, b)
    if diff.getbbox() is None:
        return 0
    lut = [0] * (tolerance + 1) + [255] * (255 - tolerance)
    mask = diff.convert("L").point(lut)
    return mask.histogram()[255]


class _Rate:
    """Sliding window over the last N seconds: rate and bytes per second."""

    def __init__(self, span: float = 6.0) -> None:
        self.span = span
        self._ev: Deque[Tuple[float, int, int]] = collections.deque()

    def add(self, t: float, nbytes: int, frames: int = 1) -> None:
        self._ev.append((t, nbytes, frames))
        while self._ev and t - self._ev[0][0] > self.span:
            self._ev.popleft()

    def rates(self, now: float) -> Tuple[float, float, float]:
        ev = self._ev
        while ev and now - ev[0][0] > self.span:
            ev.popleft()
        if len(ev) < 2:
            return 0.0, 0.0, 0.0
        dt = max(1e-3, ev[-1][0] - ev[0][0])
        tail = list(ev)[1:]
        return len(tail) / dt, sum(e[1] for e in tail) / 1024 / dt, sum(e[2] for e in tail) / dt


PreviewCallback = Callable[[Image.Image, EncodedFrame, UploadResult], None]


class MirrorEngine:
    def __init__(self, device: MiniTooDevice, config: MirrorConfig,
                 on_preview: Optional[PreviewCallback] = None,
                 on_stats: Optional[Callable[[MirrorStats], None]] = None,
                 on_stopped: Optional[Callable[[str], None]] = None,
                 grabber_factory: Optional[Callable[[], ScreenGrabber]] = None) -> None:
        self.device = device
        self._cfg = config
        self._cfg_version = 0
        self.on_preview = on_preview
        self.on_stats = on_stats
        self.on_stopped = on_stopped
        self._grabber_factory = grabber_factory or ScreenGrabber
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._cond = threading.Condition()
        self._frames: Deque[_Captured] = collections.deque(maxlen=16)
        self._seq = 0
        self._fatal = ""
        self._cap_status = ""
        self._upload_s = 0.2               # average upload time (paces the capture)
        self.stats = MirrorStats()
        self._rate = _Rate()

    # ---------------------------------------------------------------- control

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def config(self) -> MirrorConfig:
        return self._cfg

    def update_config(self, config: MirrorConfig) -> None:
        """Change settings while running - used from the next frame on."""
        with self._lock:
            self._cfg = config
            self._cfg_version += 1

    def _snapshot(self) -> Tuple[MirrorConfig, int]:
        with self._lock:
            return self._cfg, self._cfg_version

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._fatal = ""
        self._cap_status = ""
        self._frames.clear()
        self.stats = MirrorStats(running=True, status=_("Starting…"))
        self._rate = _Rate()
        self._thread = threading.Thread(target=self._run, name="minitoo-mirror", daemon=True)
        self._thread.start()

    def stop(self, wait: bool = True) -> None:
        self._stop.set()
        with self._cond:
            self._cond.notify_all()
        th = self._thread
        if wait and th is not None and th is not threading.current_thread():
            th.join(15.0)

    # ------------------------------------------------------------------- util

    def _status(self, text: str) -> None:
        if self.stats.status != text:
            self.stats.status = text
            self._publish()

    def _publish(self) -> None:
        cb = self.on_stats
        if cb is not None:
            try:
                cb(replace(self.stats))
            except Exception:  # pragma: no cover
                log.exception("on_stats")

    def _update_rates(self, now: float) -> None:
        fps, kbps, shown = self._rate.rates(now)
        self.stats.fps, self.stats.kbps, self.stats.shown_fps = fps, kbps, shown

    # ---------------------------------------------------------------- threads

    def _run(self) -> None:
        cap = threading.Thread(target=self._capture_loop, name="minitoo-capture", daemon=True)
        cap.start()
        try:
            self._send_loop()
        except Exception as e:  # pragma: no cover
            log.exception("mirror error")
            self._fatal = self._fatal or _("Stopped because of an error: {err}", err=e)
        finally:
            self._stop.set()
            with self._cond:
                self._cond.notify_all()
            cap.join(5.0)
            reason = self._fatal
            self.stats.running = False
            self.stats.status = reason or _("Stopped")
            self._publish()
            cb = self.on_stopped
            if cb is not None:
                cb(reason)

    def _capture_loop(self) -> None:
        """Continuous capture. Pace: the target FPS, but not much faster than
        Bluetooth can deliver (saves CPU)."""
        grabber = None
        try:
            grabber = self._grabber_factory()
            version = -1
            next_t = time.perf_counter()
            while not self._stop.is_set():
                cfg, ver = self._snapshot()
                if ver != version:
                    version = ver
                    grabber.refresh_monitors()
                fmt = FORMATS.get(cfg.fmt_key, FORMATS["jpeg160"])
                t0 = time.perf_counter()
                try:
                    raw, cursor, _rect = grabber.grab(cfg.source, cfg.image.show_cursor)
                    img = process_frame(raw, (fmt.width, fmt.height), cfg.image, cursor)
                except WindowGone as e:
                    self._fatal = _("Stopped: {reason}", reason=e)
                    self._stop.set()
                    break
                except SourceUnavailable as e:
                    self._cap_status = str(e)
                    self._stop.wait(0.5)
                    continue
                except CaptureError as e:
                    self._cap_status = _("Capture error: {err}", err=e)
                    self.stats.errors += 1
                    self._stop.wait(0.5)
                    continue
                self._cap_status = ""
                self.stats.capture_ms = (time.perf_counter() - t0) * 1000
                with self._cond:
                    self._seq += 1
                    self._frames.append(_Captured(self._seq, t0, img, ver))
                    self._cond.notify_all()

                if cfg.batch_frames > 1:
                    period = cfg.batch_interval_ms / 1000
                else:
                    fps = max(0.2, min(MAX_FPS, cfg.target_fps))
                    period = max(1.0 / fps, min(0.5, self._upload_s * 0.5))
                next_t += period
                now = time.perf_counter()
                if next_t < now - period:
                    next_t = now
                self._stop.wait(max(0.0, next_t - now))
        except Exception as e:  # pragma: no cover
            log.exception("capture thread")
            self._fatal = _("Stopped because of an error: {err}", err=e)
            self._stop.set()
        finally:
            if grabber is not None:
                try:
                    grabber.close()
                except Exception:
                    pass
            with self._cond:
                self._cond.notify_all()

    def _send_loop(self) -> None:
        encoder: Optional[FrameEncoder] = None
        version = -1
        last_sent: Optional[Image.Image] = None
        last_sent_t = 0.0
        last_seq = 0
        last_publish = 0.0
        t_prev_upload = 0.0

        while not self._stop.is_set():
            cfg, ver = self._snapshot()
            if ver != version or encoder is None:
                version = ver
                encoder = FrameEncoder(cfg.fmt_key, cfg.jpeg_quality, cfg.max_frame_kb * 1024,
                                       cfg.zstd_level, cfg.chroma_444)
                last_sent = None

            if not self.device.is_connected:
                self._status(_("Waiting for the device connection…"))
                self.device.wait_connected(0.5)
                continue

            need = max(1, cfg.batch_frames)
            # FPS limit: wait first, then take the newest frame (less latency)
            if need == 1:
                fps = max(0.2, min(MAX_FPS, cfg.target_fps))
                wait = t_prev_upload + 1.0 / fps - time.perf_counter()
                if wait > 0 and self._stop.wait(wait):
                    break
            with self._cond:
                def ready() -> bool:
                    return self._stop.is_set() or sum(
                        1 for f in self._frames if f.seq > last_seq and f.version == ver) >= need
                self._cond.wait_for(ready, timeout=0.5)
                fresh = [f for f in self._frames if f.seq > last_seq and f.version == ver]
            if self._stop.is_set():
                break
            if len(fresh) < need:
                if self._cap_status:
                    self._status(self._cap_status)
                continue
            chosen = fresh[-need:]
            last_seq = chosen[-1].seq
            frames = [c.img for c in chosen]
            t0 = chosen[0].t

            # --- unchanged frame?
            now = time.perf_counter()
            if cfg.skip_unchanged and last_sent is not None and need == 1:
                keepalive_due = cfg.keepalive_s > 0 and now - last_sent_t >= cfg.keepalive_s
                if not keepalive_due and changed_pixels(frames[0], last_sent) < cfg.change_pixels:
                    self.stats.skipped += 1
                    if now - last_publish > 0.5:
                        self._update_rates(now)
                        self.stats.status = _("No change on screen — waiting")
                        self._publish()
                        last_publish = now
                    continue

            # --- encode + upload
            speed = cfg.batch_interval_ms if len(frames) > 1 else None
            ef = encoder.encode(frames, speed_ms=speed, captured_at=t0)
            self.stats.encode_ms = ef.encode_ms
            t_prev_upload = time.perf_counter()
            try:
                res = self.device.upload(ef.payload, ef.fmt.start_extra)
            except NotConnected:
                continue
            except UploadError as e:
                self.stats.errors += 1
                self._status(str(e))
                self._stop.wait(1.0)
                continue
            done = time.perf_counter()
            self._upload_s = 0.7 * self._upload_s + 0.3 * (res.total_ms / 1000)

            st = self.stats
            st.upload_ms = res.total_ms
            st.frame_bytes = len(ef.payload)
            st.quality = ef.quality
            st.interval_ms = res.interval_ms
            st.resends += res.resends
            st.acked = res.acked
            if res.ok:
                st.uploads += 1
                st.latency_ms = (done - t0) * 1000
                last_sent = frames[-1]
                last_sent_t = done
                self._rate.add(done, len(ef.payload), len(frames))
                st.status = _("Running")
            else:
                st.errors += 1
                st.status = _("Upload failed: {err}", err=res.error)
                log.warning("upload: %s", res.summary())
            self._update_rates(done)
            last_publish = done
            if self.on_preview is not None:
                try:
                    self.on_preview(ef.preview, ef, res)
                except Exception:  # pragma: no cover
                    log.exception("on_preview")
            self._publish()


def send_still(device: MiniTooDevice, image: Image.Image, opts: ImageOptions,
               fmt_key: str = "jpeg160", jpeg_quality: int = 95,
               progress: Optional[Callable[[int, int], None]] = None,
               chroma_444: bool = True) -> Tuple[EncodedFrame, UploadResult]:
    """Send one still image (blocking)."""
    enc = FrameEncoder(fmt_key, jpeg_quality, chroma_444=chroma_444)
    fmt = enc.fmt
    img = process_frame(image.convert("RGB"), (fmt.width, fmt.height), opts, None)
    ef = enc.encode([img])
    return ef, device.upload(ef.payload, fmt.start_extra, progress)


def send_animation(device: MiniTooDevice, frames: List[Image.Image], opts: ImageOptions,
                   speed_ms: int, fmt_key: str = "jpeg160", jpeg_quality: int = 85,
                   max_bytes: int = 300_000, chroma_444: bool = True,
                   progress: Optional[Callable[[int, int], None]] = None
                   ) -> Tuple[EncodedFrame, UploadResult]:
    """Send a GIF / animation - its size is kept under max_bytes (quality drops)."""
    enc = FrameEncoder(fmt_key, jpeg_quality, max_frame_bytes=max_bytes, chroma_444=chroma_444)
    fmt = enc.fmt
    prepared = [process_frame(f.convert("RGB"), (fmt.width, fmt.height), opts, None)
                for f in frames[:255]]
    ef = enc.encode(prepared, speed_ms=speed_ms)
    return ef, device.upload(ef.payload, fmt.start_extra, progress)
