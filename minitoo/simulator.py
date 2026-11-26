"""Virtual MiniToo - for tests and for trying the program without the device.

Behaves like the public sources describe the real device (and like the real
device behaved in our tests): answers a start packet with "send all"
(04 8B 55 00 01), collects 256-byte chunks, asks for missing ones
(04 8B 55 01 <idx>) and finally confirms (04 BD 55 13 01 05 00).
The transfer speed is simulated.

Note: simulator numbers do not reflect the speed of a real device.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from PIL import Image

from .protocol import (CMD_EXTENDED, CMD_NEW_GIF, CMD_SYSTEM_BRIGHT, CHUNK_SIZE, FORMATS,
                       FrameParser, chunk_count, encode_device_frame, parse_payload)
from .transport import ConnectionLost, Transport, TransportError

GO_AHEAD = bytes([CMD_NEW_GIF, 0x55, 0x00, 0x01])
UPLOAD_DONE = bytes([CMD_EXTENDED, 0x55, 0x13, 0x01, 0x05, 0x00])


@dataclass
class FakeMiniToo:
    bytes_per_sec: float = 40_000.0      # 0 = no delay (tests); real device ~35-40 KB/s
    decode_ms: float = 40.0
    send_go_ahead: bool = True
    send_ack: bool = True
    drop_once: Set[int] = field(default_factory=set)   # "lose" these chunks the first time
    fail_connects: int = 0               # how many connection attempts fail
    disconnect_after_uploads: int = 0    # "drop" the link after every N uploads (0 = never)
    ignore_starts: int = 0               # ignore (do not answer) this many start packets

    brightness: int = -1
    uploads: int = 0
    last_payload: bytes = b""
    last_image: Optional[Image.Image] = None
    last_error: str = ""
    connects: int = 0
    log: List[str] = field(default_factory=list)

    def handle(self, frame, out: "queue.Queue[bytes]", link: "SimulatedTransport") -> None:
        if frame.cmd == CMD_SYSTEM_BRIGHT and frame.body:
            self.brightness = frame.body[0]
            out.put(encode_device_frame(0x04, bytes([CMD_SYSTEM_BRIGHT, 0x55])))
            return
        if frame.cmd != CMD_NEW_GIF or not frame.body:
            self.log.append(f"unknown command 0x{frame.cmd:02X}")
            return
        body = frame.body
        if body[0] == 0x00:                                   # start
            if self.ignore_starts > 0:
                self.ignore_starts -= 1
                link.total = -1                               # reject the chunks
                return
            link.total = int.from_bytes(body[1:5], "little")
            link.extra = bytes(body[5:])
            link.parts = {}
            link.requested = set()
            if self.send_go_ahead:
                out.put(encode_device_frame(0x04, GO_AHEAD))
            return
        if body[0] == 0x01 and len(body) >= 7:                # chunk
            total = int.from_bytes(body[1:5], "little")
            idx = int.from_bytes(body[5:7], "little")
            data = bytes(body[7:])
            if total != link.total:
                self.last_error = f"chunk length {total} != start {link.total}"
                return
            if idx in self.drop_once:
                self.drop_once.discard(idx)
                link.dropped.add(idx)
            else:
                n = chunk_count(total)
                expected = CHUNK_SIZE if idx < n - 1 else total - CHUNK_SIZE * (n - 1)
                if len(data) != expected:
                    self.last_error = f"chunk {idx}: {len(data)} B, expected {expected}"
                    return
                link.parts[idx] = data
            self._check_complete(out, link, idx)

    def _check_complete(self, out, link, idx) -> None:
        n = chunk_count(link.total)
        if len(link.parts) == n:
            payload = b"".join(link.parts[i] for i in range(n))
            link.parts = {}
            self._show(payload, link.extra)
            if self.decode_ms:
                time.sleep(self.decode_ms / 1000)
            if self.send_ack:
                out.put(encode_device_frame(0x04, UPLOAD_DONE))
            if self.disconnect_after_uploads and self.uploads % self.disconnect_after_uploads == 0:
                link.broken = True
            return
        if idx == n - 1 or idx in link.requested:
            missing = [i for i in range(n) if i not in link.parts]
            if missing:
                m = missing[0]
                link.requested.add(m)
                out.put(encode_device_frame(0x04, bytes([CMD_NEW_GIF, 0x55, 0x01])
                                            + m.to_bytes(4, "little")))

    def _show(self, payload: bytes, extra: bytes) -> None:
        self.uploads += 1
        self.last_payload = payload
        try:
            p = parse_payload(payload)
            fmt = next(f for f in FORMATS.values()
                       if (f.rows, f.cols) == (p.rows, p.cols)
                       and f.encoding == ("jpeg" if p.marker == 0x23 else "zstd"))
            if fmt.start_extra != extra:
                self.log.append(f"start extra {extra.hex()} != {fmt.start_extra.hex()}")
            from .encoder import decode_payload_image
            self.last_image = decode_payload_image(payload, fmt, p.frame_count - 1)
            self.last_error = ""
        except Exception as e:
            self.last_error = f"payload could not be decoded: {e}"
            self.log.append(self.last_error)


DEFAULT_DEVICE = FakeMiniToo()


class SimulatedTransport(Transport):
    name = "simulator"

    def __init__(self, device: Optional[FakeMiniToo] = None) -> None:
        self.device = device or DEFAULT_DEVICE
        self._out: "queue.Queue[bytes]" = queue.Queue()
        self._parser = FrameParser()
        self._open = False
        self._lock = threading.Lock()
        self.total = 0
        self.extra = b""
        self.parts: Dict[int, bytes] = {}
        self.requested: Set[int] = set()
        self.dropped: Set[int] = set()
        self.broken = False

    def describe(self) -> str:
        from .i18n import _
        return _("Simulator (virtual MiniToo)")

    def open(self) -> None:
        dev = self.device
        time.sleep(0.05 if dev.bytes_per_sec else 0)
        if dev.fail_connects > 0:
            dev.fail_connects -= 1
            raise TransportError("simulator: connection failed (simulated)")
        dev.connects += 1
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def send(self, data: bytes) -> None:
        if not self._open or self.broken:
            self._open = False
            raise ConnectionLost("simulator: link lost")
        bps = self.device.bytes_per_sec
        if bps:
            time.sleep(len(data) / bps)
        with self._lock:
            for frame in self._parser.feed(data):
                self.device.handle(frame, self._out, self)

    def recv(self, timeout: float) -> Optional[bytes]:
        if not self._open:
            raise ConnectionLost("simulator: link closed")
        if self.broken:
            self._open = False
            raise ConnectionLost("simulator: link lost (simulated)")
        try:
            return self._out.get(timeout=max(0.0, min(timeout, 0.25)))
        except queue.Empty:
            return None
