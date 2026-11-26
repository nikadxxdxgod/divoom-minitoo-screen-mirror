"""Turns images into MiniToo payloads (JPEG or RGB888 + zstd)."""

from __future__ import annotations

import io
import time
from dataclasses import dataclass, field
from typing import Optional, Sequence, Tuple

from PIL import Image

from .protocol import (FORMATS, DisplayFormat, build_jpeg_payload, build_zstd_payload,
                       chunk_count)

ZSTD_WINDOW_LOG = 17   # [H][A]: with a larger window the device acks but the screen stays black


# ---------------------------------------------------------------------------
# zstd: Python 3.14+ has compression.zstd built in, otherwise the zstandard package
# ---------------------------------------------------------------------------

def _make_zstd():
    try:
        from compression import zstd as _z  # Python 3.14+

        def compress(data: bytes, level: int) -> bytes:
            cp = _z.CompressionParameter
            c = _z.ZstdCompressor(options={cp.compression_level: level,
                                           cp.window_log: ZSTD_WINDOW_LOG,
                                           cp.content_size_flag: 1})
            c.set_pledged_input_size(len(data))
            return c.compress(data, mode=_z.ZstdCompressor.FLUSH_FRAME)

        def decompress(data: bytes) -> bytes:
            return _z.decompress(data)

        return "compression.zstd (zstd " + _z.zstd_version + ")", compress, decompress
    except ImportError:
        pass
    try:
        import zstandard as _zs

        def compress(data: bytes, level: int) -> bytes:
            params = _zs.ZstdCompressionParameters.from_level(
                level, window_log=ZSTD_WINDOW_LOG, write_content_size=True)
            return _zs.ZstdCompressor(compression_params=params).compress(data)

        def decompress(data: bytes) -> bytes:
            return _zs.ZstdDecompressor().decompress(data, max_output_size=64 * 1024 * 1024)

        return "zstandard " + _zs.__version__, compress, decompress
    except ImportError:
        return None, None, None


ZSTD_BACKEND, zstd_compress, zstd_decompress = _make_zstd()


def zstd_window_size(frame: bytes) -> int:
    """Window size from a zstd frame header (tests: must be <= 128 KiB)."""
    if frame[:4] != b"\x28\xb5\x2f\xfd":
        raise ValueError("not a zstd frame")
    fhd = frame[4]
    single_segment = (fhd >> 5) & 1
    fcs_flag = fhd >> 6
    did_flag = fhd & 3
    pos = 5
    if not single_segment:
        wd = frame[pos]
        exponent, mantissa = wd >> 3, wd & 7
        base = 1 << (10 + exponent)
        return base + (base // 8) * mantissa
    pos += (0, 1, 2, 4)[did_flag]
    size_len = (1, 2, 4, 8)[fcs_flag]
    size = int.from_bytes(frame[pos:pos + size_len], "little")
    return size + 256 if size_len == 2 else size


# ---------------------------------------------------------------------------

@dataclass
class EncodedFrame:
    payload: bytes
    fmt: DisplayFormat
    frame_count: int
    preview: Image.Image            # exactly what the device shows (incl. JPEG artefacts)
    quality: int = 0                # JPEG quality actually used
    encode_ms: float = 0.0
    captured_at: float = 0.0        # time.perf_counter() at capture
    meta: dict = field(default_factory=dict)

    @property
    def chunks(self) -> int:
        return chunk_count(len(self.payload))


class FrameEncoder:
    """JPEG quality + optional maximum frame size: if a JPEG is larger than
    the budget, quality is lowered automatically (fewer bytes = more FPS)."""

    MIN_AUTO_QUALITY = 20

    def __init__(self, fmt_key: str = "jpeg160", jpeg_quality: int = 70,
                 max_frame_bytes: int = 0, zstd_level: int = 9, chroma_444: bool = True) -> None:
        self.configure(fmt_key, jpeg_quality, max_frame_bytes, zstd_level, chroma_444)

    def configure(self, fmt_key: str, jpeg_quality: int, max_frame_bytes: int,
                  zstd_level: int, chroma_444: bool = True) -> None:
        if fmt_key not in FORMATS:
            raise ValueError(f"unknown format: {fmt_key}")
        self.fmt = FORMATS[fmt_key]
        self.jpeg_quality = max(5, min(100, int(jpeg_quality)))
        self.max_frame_bytes = max(0, int(max_frame_bytes))
        self.zstd_level = max(1, min(19, int(zstd_level)))
        # 4:4:4 = colour at full resolution (sharp coloured text). Verified on a
        # real MiniToo (2026-11-26). 4:2:0 = smaller file.
        self.chroma_444 = bool(chroma_444)
        if self.fmt.encoding == "zstd" and zstd_compress is None:
            raise RuntimeError("zstd is not available - pip install zstandard")

    @property
    def size(self):
        return self.fmt.width, self.fmt.height

    def _jpeg(self, img: Image.Image, quality: int) -> bytes:
        buf = io.BytesIO()
        # baseline JPEG (no progressive). subsampling: 0 = 4:4:4, 2 = 4:2:0 ([G]/[B])
        img.save(buf, format="JPEG", quality=quality, subsampling=0 if self.chroma_444 else 2)
        return buf.getvalue()

    def _jpeg_budget(self, img: Image.Image, budget: int) -> Tuple[bytes, int]:
        q = self.jpeg_quality
        data = self._jpeg(img, q)
        if budget <= 0:
            return data, q
        while len(data) > budget and q > self.MIN_AUTO_QUALITY:
            # big overshoot -> big step
            step = 15 if len(data) > budget * 1.6 else (8 if len(data) > budget * 1.2 else 4)
            q = max(self.MIN_AUTO_QUALITY, q - step)
            data = self._jpeg(img, q)
        return data, q

    def encode(self, images: Sequence[Image.Image], speed_ms: Optional[int] = None,
               captured_at: float = 0.0) -> EncodedFrame:
        if not images:
            raise ValueError("no image")
        t0 = time.perf_counter()
        fmt = self.fmt
        frames = [im if im.size == self.size and im.mode == "RGB" else
                  im.convert("RGB").resize(self.size) for im in images]
        n = len(frames)
        speed = fmt.still_speed_ms if (speed_ms is None and n == 1) else int(speed_ms or 100)

        if fmt.encoding == "jpeg":
            budget = (self.max_frame_bytes // n) if self.max_frame_bytes else 0
            jpegs, qualities = [], []
            for im in frames:
                data, q = self._jpeg_budget(im, budget)
                jpegs.append(data)
                qualities.append(q)
            payload = build_jpeg_payload(jpegs, speed, fmt)
            preview = Image.open(io.BytesIO(jpegs[-1]))
            preview.load()
            preview = preview.convert("RGB")
            quality = min(qualities)
        else:
            raw = b"".join(im.tobytes("raw", "RGB") for im in frames)
            blob = zstd_compress(raw, self.zstd_level)
            payload = build_zstd_payload(blob, n, speed, fmt)
            preview = frames[-1]
            quality = 100
        return EncodedFrame(payload=payload, fmt=fmt, frame_count=n, preview=preview,
                            quality=quality, encode_ms=(time.perf_counter() - t0) * 1000,
                            captured_at=captured_at or t0)


def decode_payload_image(payload: bytes, fmt: DisplayFormat, index: int = 0) -> Image.Image:
    """Recover a frame from a payload (simulator / tests)."""
    from .protocol import parse_payload

    p = parse_payload(payload)
    if p.marker == 0x23:
        img = Image.open(io.BytesIO(p.frames[index]))
        img.load()
        return img.convert("RGB")
    raw = zstd_decompress(p.frames[0])
    w, h = p.cols * 16, p.rows * 16
    per = w * h * 3
    if len(raw) != per * p.frame_count:
        raise ValueError(f"zstd: expected {per * p.frame_count} bytes, got {len(raw)}")
    return Image.frombytes("RGB", (w, h), raw[index * per:(index + 1) * per])
