"""Divoom MiniToo Bluetooth (SPP / RFCOMM) protocol.

There is no official documentation. Everything here comes from public
reverse-engineering work whose authors tested it on a real MiniToo:

  [G] gist.github.com/alphafornow/8d38848adf9be12d0f9dc7700dff5e21
      JPEG, 160x128, header 0x23, grid 8x10, start packet with circle_flag.
  [B] github.com/bugzmanov/divoom-minitoo (FINDINGS.md)
      same JPEG path, exactly 256-byte chunks, "sleep" commands are dangerous.
  [H] github.com/d03n3rfr1tz3/hass-divoom PR #78 (MIT)
      RGB888 + zstd (window_log=17), header 0x25, 128x128 (grid 8x8).
  [A] github.com/alvinunreal/divoom-minitoo-osx (PROTOCOL.md)
      the same 0x25 format and the device's final acknowledgement.

Verified on a real MiniToo with this program (2026-11-26): RFCOMM channel 1,
JPEG 160x128 (4:2:0 and 4:4:4) fills the screen, the device answers every
upload with "send all" and a final acknowledgement.

Packet layout (both directions):

    01 | len_lo len_hi | cmd | body... | sum_lo sum_hi | 02

    len = len(body) + 3                 (cmd + 2 checksum bytes)
    sum = (len_lo + len_hi + cmd + body) & 0xFFFF, little-endian
    no byte escaping (current firmware) [G][B]

Device reply:  01 | len | 04 | <command it answers> | 55 | data... | sum | 02
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

from .i18n import N_

FRAME_START = 0x01
FRAME_END = 0x02

CMD_RESPONSE = 0x04       # envelope of every device reply
CMD_SYSTEM_BRIGHT = 0x74  # brightness 0-100, "CONFIRMED WORKING" in [G]
CMD_NEW_GIF = 0x8B        # SPP_APP_NEW_GIF_CMD2020 - image/animation upload
CMD_EXTENDED = 0xBD       # extended commands (only seen in replies here)

ACK_MARKER = 0x55
CHUNK_SIZE = 256          # [B]: 250-byte chunks corrupt every image - always 256

# The program only ever sends these commands. Everything else is blocked:
# according to [B] some commands (0x40, 0xA3, 0xA4, 0xAD, 0xAE - the "sleep
# family") leave the device in a state that only a power cycle fixes, and
# 0x3C crashes it.
ALLOWED_COMMANDS = frozenset({CMD_NEW_GIF, CMD_SYSTEM_BRIGHT})
KNOWN_DANGEROUS_COMMANDS = frozenset({0x40, 0xA3, 0xA4, 0xAD, 0xAE, 0x3C})

# [B]: a 572 KB animation worked, at 763 KB the device rebooted.
MAX_SAFE_PAYLOAD = 400_000


class ProtocolError(Exception):
    """A packet could not be built or parsed."""


class UnsafeCommandError(ProtocolError):
    """The command is not on the allow-list and will not be sent."""


def checksum16(data: bytes) -> int:
    return sum(data) & 0xFFFF


def _encode_frame(cmd: int, body: bytes) -> bytes:
    length = len(body) + 3
    if length > 0xFFFF:
        raise ProtocolError(f"packet too large: {len(body)} bytes")
    out = bytearray(len(body) + 7)
    out[0] = FRAME_START
    out[1] = length & 0xFF
    out[2] = (length >> 8) & 0xFF
    out[3] = cmd & 0xFF
    out[4:4 + len(body)] = body
    csum = checksum16(memoryview(out)[1:4 + len(body)])
    out[-3] = csum & 0xFF
    out[-2] = (csum >> 8) & 0xFF
    out[-1] = FRAME_END
    return bytes(out)


def build_frame(cmd: int, body: bytes = b"") -> bytes:
    """Build an outgoing packet. Only allow-listed commands."""
    if cmd not in ALLOWED_COMMANDS:
        raise UnsafeCommandError(
            f"command 0x{cmd:02X} is blocked (allowed: "
            + ", ".join(f"0x{c:02X}" for c in sorted(ALLOWED_COMMANDS)) + ")")
    return _encode_frame(cmd, bytes(body))


def encode_device_frame(cmd: int, body: bytes = b"") -> bytes:
    """Build a packet as the *device* would send it - tests and simulator only.
    Nothing built here is ever sent to a device."""
    return _encode_frame(cmd, bytes(body))


# ---------------------------------------------------------------------------
# Incoming packets
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Frame:
    cmd: int
    body: bytes
    raw: bytes

    @property
    def is_response(self) -> bool:
        return (self.cmd == CMD_RESPONSE and len(self.body) >= 2
                and self.body[1] == ACK_MARKER)

    @property
    def echoed_cmd(self) -> Optional[int]:
        return self.body[0] if self.is_response else None

    @property
    def data(self) -> bytes:
        return self.body[2:] if self.is_response else self.body

    def hex(self, limit: int = 48) -> str:
        return hex_dump(self.raw, limit)


class FrameParser:
    """Cuts packets out of a byte stream. Recovers from partial data, garbage
    between packets, bad checksums and false start bytes."""

    MAX_LEN = 8192

    def __init__(self) -> None:
        self._buf = bytearray()
        self.garbage_bytes = 0
        self.bad_frames = 0

    def reset(self) -> None:
        self._buf.clear()

    def feed(self, data: bytes) -> List[Frame]:
        buf = self._buf
        buf += data
        frames: List[Frame] = []
        while True:
            start = buf.find(FRAME_START)
            if start < 0:
                self.garbage_bytes += len(buf)
                buf.clear()
                break
            if start:
                self.garbage_bytes += start
                del buf[:start]
            if len(buf) < 3:
                break
            length = buf[1] | (buf[2] << 8)
            if length < 3 or length > self.MAX_LEN:
                self.garbage_bytes += 1
                del buf[0]
                continue
            total = length + 4
            if len(buf) < total:
                # Maybe a false start (0x01 inside data) while a complete,
                # valid packet already sits further on - then skip ahead.
                nxt = self._next_valid(buf)
                if nxt > 0:
                    self.garbage_bytes += nxt
                    del buf[:nxt]
                    continue
                break
            if buf[total - 1] != FRAME_END:
                self.bad_frames += 1
                del buf[0]
                continue
            expected = buf[total - 3] | (buf[total - 2] << 8)
            if checksum16(memoryview(buf)[1:total - 3]) != expected:
                self.bad_frames += 1
                del buf[0]
                continue
            raw = bytes(buf[:total])
            frames.append(Frame(cmd=raw[3], body=raw[4:total - 3], raw=raw))
            del buf[:total]
        return frames

    def _complete_at(self, buf: bytearray, j: int) -> bool:
        if len(buf) - j < 7 or buf[j] != FRAME_START:
            return False
        length = buf[j + 1] | (buf[j + 2] << 8)
        if length < 3 or length > self.MAX_LEN:
            return False
        end = j + length + 4
        if end > len(buf) or buf[end - 1] != FRAME_END:
            return False
        expected = buf[end - 3] | (buf[end - 2] << 8)
        return checksum16(memoryview(buf)[j + 1:end - 3]) == expected

    def _next_valid(self, buf: bytearray) -> int:
        j = buf.find(FRAME_START, 1)
        while j > 0:
            if self._complete_at(buf, j):
                return j
            j = buf.find(FRAME_START, j + 1)
        return -1


# ---------------------------------------------------------------------------
# Image upload (0x8B)
# ---------------------------------------------------------------------------

def chunk_count(payload_len: int) -> int:
    return (payload_len + CHUNK_SIZE - 1) // CHUNK_SIZE


def build_start_frame(payload_len: int, extra: bytes = b"") -> bytes:
    """Start of upload: 8B 00 <total length u32 LE> [+ extra].
    [G] adds circle_flag=0 for JPEG, [H] does not."""
    if payload_len <= 0:
        raise ProtocolError("empty payload")
    return build_frame(CMD_NEW_GIF, b"\x00" + payload_len.to_bytes(4, "little") + extra)


def build_chunk_frames(payload: bytes) -> List[bytes]:
    """Chunks: 8B 01 <total length u32 LE> <index u16 LE> <up to 256 bytes>."""
    total = len(payload)
    if total == 0:
        raise ProtocolError("empty payload")
    n = chunk_count(total)
    if n > 0xFFFF:
        raise ProtocolError("payload too large")
    total_le = total.to_bytes(4, "little")
    view = memoryview(payload)
    frames = []
    for i in range(n):
        body = b"\x01" + total_le + i.to_bytes(2, "little") + bytes(view[i * CHUNK_SIZE:(i + 1) * CHUNK_SIZE])
        frames.append(build_frame(CMD_NEW_GIF, body))
    return frames


def build_brightness_frame(level: int) -> bytes:
    """Brightness 0-100 ([G]: 0x74, "CONFIRMED WORKING")."""
    level = max(0, min(100, int(level)))
    return build_frame(CMD_SYSTEM_BRIGHT, bytes([level]))


class Reply(Enum):
    SEND_ALL = "send_all"        # 04 8B 55 00 ...  - "ready, send everything"
    RESEND = "resend"            # 04 8B 55 01 <idx> - "send chunk idx"
    UPLOAD_DONE = "upload_done"  # 04 BD 55 13 ...  - upload finished [A]
    OTHER = "other"


def classify_reply(frame: Frame, n_chunks: int = 0) -> Tuple[Reply, int]:
    """Returns (reply kind, chunk index or -1)."""
    if not frame.is_response:
        return Reply.OTHER, -1
    echoed = frame.echoed_cmd
    data = frame.data
    if echoed == CMD_NEW_GIF and data:
        ctrl = data[0]
        if ctrl == 0x00:
            return Reply.SEND_ALL, -1
        if ctrl == 0x01:
            rest = data[1:]
            idx = -1
            # [G]: 4-byte index (LE), [B]: 2-byte index (LE) - accept both.
            if len(rest) >= 4:
                idx = int.from_bytes(rest[:4], "little")
            if (idx < 0 or (n_chunks and idx >= n_chunks)) and len(rest) >= 2:
                idx = int.from_bytes(rest[:2], "little")
            return Reply.RESEND, idx
        return Reply.OTHER, -1
    if echoed == CMD_EXTENDED and data[:1] == b"\x13":
        return Reply.UPLOAD_DONE, -1
    return Reply.OTHER, -1


# ---------------------------------------------------------------------------
# Display formats and payload header
# ---------------------------------------------------------------------------

JPEG_MARKER = 0x23
ZSTD_MARKER = 0x25
FRAME_TYPE_JPEG = 0x01


@dataclass(frozen=True)
class DisplayFormat:
    key: str
    width: int
    height: int
    rows: int           # height in 16-pixel blocks
    cols: int           # width in 16-pixel blocks
    encoding: str       # "jpeg" | "zstd"
    start_extra: bytes  # extra bytes in the start packet
    still_speed_ms: int  # "speed" of a single still image (as in the source)
    label: str          # English; translated in the UI
    source: str


FORMATS: Dict[str, DisplayFormat] = {
    "jpeg160": DisplayFormat(
        key="jpeg160", width=160, height=128, rows=8, cols=10, encoding="jpeg",
        start_extra=b"\x00", still_speed_ms=0,
        label=N_("JPEG 160×128 — full screen (recommended)"),
        source="[G] alphafornow gist, [B] bugzmanov/divoom-minitoo"),
    "zstd128": DisplayFormat(
        key="zstd128", width=128, height=128, rows=8, cols=8, encoding="zstd",
        start_extra=b"", still_speed_ms=1000,
        label=N_("RGB+zstd 128×128 — lossless, not full width (untested)"),
        source="[H] hass-divoom PR #78, [A] alvinunreal/divoom-minitoo-osx"),
}
DEFAULT_FORMAT = "jpeg160"


def build_jpeg_payload(jpeg_frames: Sequence[bytes], speed_ms: int,
                       fmt: DisplayFormat = FORMATS["jpeg160"]) -> bytes:
    """23 | frames | speed u16 BE | rows | cols  +  per frame: 01 | len u32 BE | JPEG"""
    n = len(jpeg_frames)
    if not 1 <= n <= 255:
        raise ProtocolError("frame count must be 1-255")
    parts = [bytes([JPEG_MARKER, n]), _speed(speed_ms), bytes([fmt.rows, fmt.cols])]
    for jpg in jpeg_frames:
        parts.append(bytes([FRAME_TYPE_JPEG]))
        parts.append(len(jpg).to_bytes(4, "big"))
        parts.append(jpg)
    return b"".join(parts)


def build_zstd_payload(zstd_blob: bytes, frame_count: int, speed_ms: int,
                       fmt: DisplayFormat = FORMATS["zstd128"]) -> bytes:
    """25 | frames | speed u16 BE | rows | cols | zstd_len u32 BE | zstd(RGB888 frames)"""
    if not 1 <= frame_count <= 255:
        raise ProtocolError("frame count must be 1-255")
    return b"".join([bytes([ZSTD_MARKER, frame_count]), _speed(speed_ms),
                     bytes([fmt.rows, fmt.cols]), len(zstd_blob).to_bytes(4, "big"), zstd_blob])


def _speed(speed_ms: int) -> bytes:
    return max(0, min(0xFFFF, int(speed_ms))).to_bytes(2, "big")


@dataclass
class ParsedPayload:
    marker: int
    frame_count: int
    speed_ms: int
    rows: int
    cols: int
    frames: List[bytes]   # JPEG: one JPEG per frame; zstd: a single compressed block


def parse_payload(payload: bytes) -> ParsedPayload:
    """Decode a payload (tests, simulator, diagnostics)."""
    if len(payload) < 6:
        raise ProtocolError("payload too short")
    marker, count = payload[0], payload[1]
    speed = int.from_bytes(payload[2:4], "big")
    rows, cols = payload[4], payload[5]
    pos = 6
    frames: List[bytes] = []
    if marker == JPEG_MARKER:
        for _ in range(count):
            if pos + 5 > len(payload) or payload[pos] != FRAME_TYPE_JPEG:
                raise ProtocolError(f"bad JPEG frame header at offset {pos}")
            ln = int.from_bytes(payload[pos + 1:pos + 5], "big")
            frames.append(payload[pos + 5:pos + 5 + ln])
            pos += 5 + ln
        if pos != len(payload):
            raise ProtocolError("trailing bytes after the last frame")
    elif marker == ZSTD_MARKER:
        ln = int.from_bytes(payload[6:10], "big")
        if 10 + ln != len(payload):
            raise ProtocolError("zstd block length mismatch")
        frames.append(payload[10:])
    else:
        raise ProtocolError(f"unknown marker 0x{marker:02X}")
    return ParsedPayload(marker, count, speed, rows, cols, frames)


def hex_dump(data: bytes, limit: int = 48) -> str:
    shown = " ".join(f"{b:02x}" for b in data[:limit])
    if len(data) > limit:
        shown += f" ... ({len(data)} B)"
    return shown
