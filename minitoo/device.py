"""Talking to the MiniToo: a reader thread for replies, the image upload state
machine (0x8B), automatic pacing and automatic reconnection.

Upload sequence ([G][B][A], verified on a real MiniToo):
    1. us:     8B 00 <length u32 LE> [+ extra]
    2. device: 04 8B 55 00 ...        "send everything"
               (or 04 8B 55 01 <idx> - "send chunk idx")
    3. us:     8B 01 <length> <idx u16> <256 bytes>  x N
    4. device: 04 BD 55 13 ...        "received, showing it"
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional

from .i18n import N_, _
from .protocol import (MAX_SAFE_PAYLOAD, Frame, FrameParser, Reply, build_brightness_frame,
                       build_chunk_frames, build_start_frame, classify_reply)
from .transport import ConnectionLost, Transport, TransportError, precise_sleep

log = logging.getLogger("minitoo.device")


class DeviceState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"


STATE_LABELS = {
    DeviceState.DISCONNECTED: N_("Disconnected"),
    DeviceState.CONNECTING: N_("Connecting…"),
    DeviceState.CONNECTED: N_("Connected"),
    DeviceState.RECONNECTING: N_("Reconnecting…"),
}


class DeviceError(Exception):
    pass


class NotConnected(DeviceError):
    pass


class UploadError(DeviceError):
    pass


@dataclass
class UploadSettings:
    # Pause between chunks. [G] used 20 ms; on a real MiniToo (2026-11-26)
    # 15 -> 0 ms were all clean, the fastest total time was at 2-4 ms.
    chunk_interval_ms: float = 4.0
    auto_pacing: bool = True             # lower/raise the interval automatically
    min_interval_ms: float = 2.0
    max_interval_ms: float = 40.0
    request_timeout: float = 1.5         # start -> "send all" (measured 50-360 ms)
    ack_timeout: float = 2.0             # last chunk -> final ack (measured 20-210 ms)
    short_wait: float = 0.3              # when the device never sends that reply
    max_resends: int = 64


@dataclass
class UploadResult:
    ok: bool = False
    payload_bytes: int = 0
    chunks: int = 0
    acked: bool = False
    go_ahead: bool = False               # did the device say "send all"
    resends: int = 0
    restreams: int = 0
    start_wait_ms: float = 0.0
    stream_ms: float = 0.0
    ack_wait_ms: float = 0.0
    total_ms: float = 0.0
    interval_ms: float = 0.0
    error: str = ""

    @property
    def kbps(self) -> float:
        return (self.payload_bytes / 1024) / (self.total_ms / 1000) if self.total_ms else 0.0

    def summary(self) -> str:
        parts = [_("{bytes} B in {ms} ms ({kbps} KB/s)", bytes=self.payload_bytes,
                   ms=round(self.total_ms), kbps=f"{self.kbps:.1f}"),
                 _("chunks: {n}", n=self.chunks)]
        if self.resends:
            parts.append(_("re-sent chunks: {n}", n=self.resends))
        parts.append(_("confirmed ✓") if self.acked else _("not confirmed"))
        if self.error:
            parts.append(self.error)
        return " · ".join(parts)


@dataclass
class DeviceStats:
    tx_bytes: int = 0
    rx_bytes: int = 0
    rx_frames: int = 0
    uploads_ok: int = 0
    uploads_failed: int = 0
    acks: int = 0
    resends: int = 0
    reconnects: int = 0
    other_frames: List[str] = field(default_factory=list)   # last unknown replies (diagnostics)


StateCallback = Callable[[DeviceState, str], None]


class MiniTooDevice:
    def __init__(self, transport_factory: Callable[[], Transport],
                 settings: Optional[UploadSettings] = None, auto_reconnect: bool = True,
                 on_state: Optional[StateCallback] = None) -> None:
        self._factory = transport_factory
        self.settings = settings or UploadSettings()
        self.auto_reconnect = auto_reconnect
        self.on_state = on_state
        self.stats = DeviceStats()
        self.last_error = ""
        self.description = ""

        self._state = DeviceState.DISCONNECTED
        self._cond = threading.Condition(threading.RLock())
        self._transport: Optional[Transport] = None
        self._parser = FrameParser()
        self._frames: "queue.Queue[Frame]" = queue.Queue()
        self._upload_lock = threading.Lock()
        self._stop = threading.Event()
        self._reconnect_thread: Optional[threading.Thread] = None

        self._interval_ms = self.settings.chunk_interval_ms
        self._clean_streak = 0
        # None = unknown yet; True = the device sends it; False = it does not
        self.ack_supported: Optional[bool] = None
        self.go_ahead_supported: Optional[bool] = None
        self._no_ack_count = 0
        self._no_go_count = 0

    # ------------------------------------------------------------------ state

    @property
    def state(self) -> DeviceState:
        return self._state

    @property
    def is_connected(self) -> bool:
        return self._state == DeviceState.CONNECTED

    @property
    def current_interval_ms(self) -> float:
        return self._interval_ms

    def _set_state(self, state: DeviceState, message: str = "") -> None:
        with self._cond:
            changed = state != self._state
            self._state = state
            self._cond.notify_all()
        if changed or message:
            log.info("%s%s", _(STATE_LABELS[state]), f" — {message}" if message else "")
        cb = self.on_state
        if cb is not None:
            try:
                cb(state, message)
            except Exception:  # pragma: no cover
                log.exception("on_state callback")

    def wait_connected(self, timeout: float) -> bool:
        end = time.monotonic() + timeout
        with self._cond:
            while self._state != DeviceState.CONNECTED:
                remaining = end - time.monotonic()
                if remaining <= 0 or self._stop.is_set():
                    return self._state == DeviceState.CONNECTED
                self._cond.wait(remaining)
            return True

    def reset_pacing(self) -> None:
        self._interval_ms = self.settings.chunk_interval_ms
        self._clean_streak = 0

    # ------------------------------------------------------------- connection

    def connect(self) -> None:
        """Blocking - call it from a background thread."""
        with self._cond:
            if self._state in (DeviceState.CONNECTED, DeviceState.CONNECTING):
                return
            self._stop.clear()
        self._set_state(DeviceState.CONNECTING)
        try:
            self._open_transport()
        except Exception as e:
            self.last_error = str(e)
            self._set_state(DeviceState.DISCONNECTED, str(e))
            raise

    def _open_transport(self) -> None:
        t = self._factory()
        self.description = t.describe()
        log.info("%s: %s", _("Connecting to"), self.description)
        t0 = time.perf_counter()
        t.open()
        with self._cond:
            if self._stop.is_set():
                t.close()
                raise NotConnected("cancelled")
            self._transport = t
            self._parser = FrameParser()
            self._drain()
        threading.Thread(target=self._reader_loop, args=(t,), name="minitoo-reader",
                         daemon=True).start()
        self.reset_pacing()
        self._set_state(DeviceState.CONNECTED,
                        f"{self.description} ({(time.perf_counter() - t0) * 1000:.0f} ms)")

    def disconnect(self) -> None:
        self._stop.set()
        with self._cond:
            t, self._transport = self._transport, None
        if t is not None:
            t.close()
        th = self._reconnect_thread
        if th is not None and th is not threading.current_thread():
            th.join(3.0)
        self._set_state(DeviceState.DISCONNECTED)

    def _on_lost(self, t: Transport, reason: str) -> None:
        with self._cond:
            if self._transport is not t:
                return                      # already handled
            self._transport = None
        try:
            t.close()
        except Exception:
            pass
        self.last_error = reason
        if self._stop.is_set():
            return
        log.warning("%s: %s", _("Connection lost"), reason)
        if self.auto_reconnect:
            self._set_state(DeviceState.RECONNECTING, reason)
            th = threading.Thread(target=self._reconnect_loop, name="minitoo-reconnect", daemon=True)
            self._reconnect_thread = th
            th.start()
        else:
            self._set_state(DeviceState.DISCONNECTED, reason)

    def _reconnect_loop(self) -> None:
        delay, attempt = 1.0, 0
        while not self._stop.wait(delay):
            attempt += 1
            self._set_state(DeviceState.RECONNECTING, _("attempt {n}", n=attempt))
            try:
                self._open_transport()
                self.stats.reconnects += 1
                return
            except Exception as e:
                self.last_error = str(e)
                log.warning("Reconnect failed: %s", e)
            delay = min(delay * 2, 15.0)

    def _reader_loop(self, t: Transport) -> None:
        try:
            while not self._stop.is_set() and self._transport is t:
                data = t.recv(0.25)
                if not data:
                    continue
                self.stats.rx_bytes += len(data)
                for f in self._parser.feed(data):
                    self.stats.rx_frames += 1
                    log.debug("RX %s", f.hex())
                    self._frames.put(f)
        except ConnectionLost as e:
            self._on_lost(t, str(e))
        except Exception as e:  # pragma: no cover
            log.exception("reader thread")
            self._on_lost(t, str(e))

    # -------------------------------------------------------------------- I/O

    def _drain(self) -> None:
        try:
            while True:
                self._frames.get_nowait()
        except queue.Empty:
            pass

    def _get_frame(self, timeout: float) -> Optional[Frame]:
        try:
            return self._frames.get(timeout=max(0.0, timeout))
        except queue.Empty:
            return None

    def _send(self, t: Transport, data: bytes) -> None:
        try:
            t.send(data)
        except ConnectionLost as e:
            self._on_lost(t, str(e))
            raise NotConnected(str(e)) from e
        self.stats.tx_bytes += len(data)

    def _require(self) -> Transport:
        t = self._transport
        if t is None or self._state != DeviceState.CONNECTED:
            raise NotConnected(_("The device is not connected."))
        return t

    def _remember_other(self, f: Frame) -> None:
        log.debug("other reply: %s", f.hex())
        self.stats.other_frames.append(f.hex(32))
        del self.stats.other_frames[:-20]

    def _wait_reply(self, n: int, timeout: float, ignore_done: bool = False):
        """Wait for "send all" / a chunk request / the final ack.
        ignore_done: while starting, skip a late ack of the previous upload."""
        end = time.perf_counter() + timeout
        while True:
            f = self._get_frame(end - time.perf_counter())
            if f is None:
                return None, -1
            kind, idx = classify_reply(f, n)
            if kind == Reply.UPLOAD_DONE and ignore_done:
                log.debug("late ack of a previous upload - skipped")
                continue
            if kind != Reply.OTHER:
                return kind, idx
            self._remember_other(f)

    # ----------------------------------------------------------------- upload

    def set_brightness(self, level: int) -> None:
        with self._upload_lock:
            t = self._require()
            self._send(t, build_brightness_frame(level))
            log.info("%s %d%%", _("Brightness"), max(0, min(100, int(level))))

    def upload(self, payload: bytes, start_extra: bytes = b"",
               progress: Optional[Callable[[int, int], None]] = None) -> UploadResult:
        """Upload one payload (image or animation). Blocking."""
        if len(payload) > MAX_SAFE_PAYLOAD:
            raise UploadError(_("The upload ({size} KB) is larger than the safe limit of {limit} KB "
                                "(bigger uploads can reboot the device).",
                                size=len(payload) // 1024, limit=MAX_SAFE_PAYLOAD // 1024))
        with self._upload_lock:
            t = self._require()
            s = self.settings
            chunks = build_chunk_frames(payload)
            n = len(chunks)
            res = UploadResult(payload_bytes=len(payload), chunks=n, interval_ms=self._interval_ms)
            self._drain()
            t0 = time.perf_counter()
            try:
                start = build_start_frame(len(payload), start_extra)
                self._send(t, start)
                wait = s.short_wait if self.go_ahead_supported is False else s.request_timeout
                kind, idx = self._wait_reply(n, wait, ignore_done=True)
                if kind is None and self.go_ahead_supported:
                    # The device normally answers - try the start once more
                    # instead of streaming "blind" (it may not be ready).
                    log.warning("No answer to the start packet - retrying")
                    self._drain()
                    self._send(t, start)
                    kind, idx = self._wait_reply(n, wait, ignore_done=True)
                    if kind is None:
                        res.start_wait_ms = (time.perf_counter() - t0) * 1000
                        res.total_ms = res.start_wait_ms
                        res.error = _("The device did not answer the start of the upload.")
                        self.stats.uploads_failed += 1
                        self._adapt_pacing(res)
                        return res
                res.start_wait_ms = (time.perf_counter() - t0) * 1000
                driven = False
                if kind == Reply.RESEND and 0 <= idx < n:
                    # the device asks for chunks one by one
                    res.go_ahead = driven = True
                    self._send(t, chunks[idx])
                else:
                    res.go_ahead = kind == Reply.SEND_ALL
                    if kind is None:
                        log.debug("no 'send all' within %.1f s - sending anyway", wait)
                    self._stream(t, chunks, res, progress)
                self._note_go_ahead(res.go_ahead)

                ack_wait = s.short_wait if self.ack_supported is False else s.ack_timeout
                ta = time.perf_counter()
                end = ta + ack_wait
                cap = s.max_resends + (n if driven else 0)
                while True:
                    remaining = end - time.perf_counter()
                    if remaining <= 0:
                        break
                    kind, idx = self._wait_reply(n, remaining)
                    if kind is None:
                        break
                    if kind == Reply.UPLOAD_DONE:
                        res.acked = True
                        break
                    if kind == Reply.RESEND:
                        if not 0 <= idx < n:
                            log.warning("device asked for a non-existent chunk %d/%d", idx, n)
                            continue
                        if res.resends >= cap:
                            res.error = _("Too many repeated chunk requests.")
                            break
                        if not driven:
                            res.resends += 1
                        self._send(t, chunks[idx])
                        end = time.perf_counter() + s.ack_timeout
                    elif kind == Reply.SEND_ALL:
                        if res.restreams >= 1:
                            res.error = _("The device asked for the whole upload again.")
                            break
                        res.restreams += 1
                        self._stream(t, chunks, res, progress)
                        end = time.perf_counter() + s.ack_timeout
                res.ack_wait_ms = (time.perf_counter() - ta) * 1000
            except NotConnected as e:
                res.error = f"{_('Connection lost')}: {e}"
                res.total_ms = (time.perf_counter() - t0) * 1000
                self.stats.uploads_failed += 1
                return res
            res.total_ms = (time.perf_counter() - t0) * 1000
            self._note_ack(res.acked)
            res.ok = not res.error and (res.acked or self.ack_supported is not True)
            if res.ok:
                self.stats.uploads_ok += 1
            else:
                self.stats.uploads_failed += 1
                if not res.error:
                    res.error = _("The device did not confirm the upload.")
            if res.acked:
                self.stats.acks += 1
            self.stats.resends += res.resends
            self._adapt_pacing(res)
            return res

    def _stream(self, t: Transport, chunks: List[bytes], res: UploadResult,
                progress: Optional[Callable[[int, int], None]]) -> None:
        interval = self._interval_ms / 1000.0
        ts = time.perf_counter()
        n = len(chunks)
        for i, frame in enumerate(chunks):
            if self._stop.is_set():
                raise NotConnected("cancelled")
            self._send(t, frame)
            if progress is not None and (i % 8 == 7 or i == n - 1):
                progress(i + 1, n)
            if interval and i < n - 1:
                precise_sleep(ts + (i + 1) * interval - time.perf_counter())
        res.stream_ms += (time.perf_counter() - ts) * 1000

    def _note_go_ahead(self, got: bool) -> None:
        if got:
            self.go_ahead_supported = True
            self._no_go_count = 0
        elif self.go_ahead_supported is None:
            self._no_go_count += 1
            if self._no_go_count >= 3:
                self.go_ahead_supported = False
                log.warning("The device never sends 'send all' - shortening the wait")

    def _note_ack(self, acked: bool) -> None:
        if acked:
            self.ack_supported = True
            self._no_ack_count = 0
        elif self.ack_supported is None:
            self._no_ack_count += 1
            if self._no_ack_count >= 3:
                self.ack_supported = False
                log.warning("The device never sends a final ack - shortening the wait "
                            "(automatic speed-up disabled)")

    def _adapt_pacing(self, res: UploadResult) -> None:
        s = self.settings
        if not s.auto_pacing:
            self._interval_ms = s.chunk_interval_ms
            return
        trouble = bool(res.resends or res.restreams or res.error
                       or (self.ack_supported and not res.acked))
        if trouble:
            self._clean_streak = 0
            self._interval_ms = min(s.max_interval_ms, max(self._interval_ms * 1.5,
                                                           self._interval_ms + 3))
            log.info("Chunk interval raised to %.1f ms", self._interval_ms)
        elif self.ack_supported:
            # Only speed up while the device confirms every upload - without
            # confirmations a damaged frame would go unnoticed.
            self._clean_streak += 1
            if self._clean_streak >= 2 and self._interval_ms > s.min_interval_ms:
                self._interval_ms = max(s.min_interval_ms, self._interval_ms - 1.0)
                self._clean_streak = 0


def make_transport_factory(kind: str, address: str = "", channel: int = 1,
                           port: str = "") -> Callable[[], Transport]:
    """kind: "rfcomm" | "serial" | "simulator"."""
    if kind == "rfcomm":
        from .transport import RfcommTransport
        return lambda: RfcommTransport(address, channel)
    if kind == "serial":
        from .transport import SerialTransport
        return lambda: SerialTransport(port)
    if kind == "simulator":
        from .simulator import SimulatedTransport
        return lambda: SimulatedTransport()
    raise ValueError(f"unknown connection kind: {kind}")


__all__ = ["DeviceState", "STATE_LABELS", "DeviceError", "NotConnected", "UploadError",
           "UploadSettings", "UploadResult", "DeviceStats", "MiniTooDevice",
           "make_transport_factory", "TransportError"]
