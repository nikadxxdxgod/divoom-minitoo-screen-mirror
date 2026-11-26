"""Preparing images for the MiniToo screen: fit / crop, rotation, colour
adjustments, mouse cursor and test images."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageStat

try:  # Pillow >= 9.1
    _R = Image.Resampling
    BILINEAR, LANCZOS, NEAREST = _R.BILINEAR, _R.LANCZOS, _R.NEAREST
except AttributeError:  # pragma: no cover - old Pillow
    BILINEAR, LANCZOS, NEAREST = Image.BILINEAR, Image.LANCZOS, Image.NEAREST

FIT_MODES = ("fit", "fill", "stretch")
ROTATIONS = (0, 90, 180, 270)


@dataclass
class ImageOptions:
    fit_mode: str = "fit"          # fit = keep aspect + bars, fill = crop, stretch = distort
    rotation: int = 0              # clockwise, degrees
    flip_h: bool = False
    contrast: float = 1.0
    saturation: float = 1.0
    sharpen: bool = False
    show_cursor: bool = True
    high_quality: bool = True      # LANCZOS (better) or BILINEAR (faster)
    background: Tuple[int, int, int] = (0, 0, 0)


@dataclass(frozen=True)
class Transform:
    """Source coordinates -> output coordinates (to draw the cursor)."""
    crop_x: float
    crop_y: float
    scale_x: float
    scale_y: float
    off_x: float
    off_y: float

    def map(self, x: float, y: float) -> Tuple[float, float]:
        return ((x - self.crop_x) * self.scale_x + self.off_x,
                (y - self.crop_y) * self.scale_y + self.off_y)


def fit_image(img: Image.Image, size: Tuple[int, int], mode: str = "fit",
              background=(0, 0, 0), high_quality: bool = True) -> Tuple[Image.Image, Transform]:
    """Resize to `size`. Always returns an RGB image of exactly that size."""
    tw, th = size
    sw, sh = img.size
    if sw <= 0 or sh <= 0:
        raise ValueError("empty image")
    if img.mode != "RGB":
        img = img.convert("RGB")
    resample = LANCZOS if high_quality else BILINEAR
    gap = 3.0 if high_quality else 2.0

    if mode == "stretch":
        out = img.resize((tw, th), resample, reducing_gap=gap)
        return out, Transform(0, 0, tw / sw, th / sh, 0, 0)

    if mode == "fill":
        # crop from the centre so the aspect ratio matches the screen
        target_ratio = tw / th
        if sw / sh > target_ratio:
            cw, ch = sh * target_ratio, float(sh)
        else:
            cw, ch = float(sw), sw / target_ratio
        cx, cy = (sw - cw) / 2, (sh - ch) / 2
        out = img.resize((tw, th), resample, box=(cx, cy, cx + cw, cy + ch), reducing_gap=gap)
        return out, Transform(cx, cy, tw / cw, th / ch, 0, 0)

    # fit - the whole image is visible, the rest is background colour
    scale = min(tw / sw, th / sh)
    nw, nh = max(1, round(sw * scale)), max(1, round(sh * scale))
    resized = img.resize((nw, nh), resample, reducing_gap=gap)
    if (nw, nh) == (tw, th):
        return resized, Transform(0, 0, nw / sw, nh / sh, 0, 0)
    out = Image.new("RGB", (tw, th), tuple(background))
    ox, oy = (tw - nw) // 2, (th - nh) // 2
    out.paste(resized, (ox, oy))
    return out, Transform(0, 0, nw / sw, nh / sh, ox, oy)


def apply_orientation(img: Image.Image, rotation: int, flip_h: bool) -> Image.Image:
    rotation %= 360
    if rotation == 90:
        img = img.transpose(Image.Transpose.ROTATE_270)   # 90° clockwise
    elif rotation == 180:
        img = img.transpose(Image.Transpose.ROTATE_180)
    elif rotation == 270:
        img = img.transpose(Image.Transpose.ROTATE_90)
    if flip_h:
        img = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return img


def enhance(img: Image.Image, contrast: float = 1.0, saturation: float = 1.0,
            sharpen: bool = False) -> Image.Image:
    if abs(contrast - 1.0) > 1e-3:
        img = ImageEnhance.Contrast(img).enhance(contrast)
    if abs(saturation - 1.0) > 1e-3:
        img = ImageEnhance.Color(img).enhance(saturation)
    if sharpen:
        img = img.filter(ImageFilter.UnsharpMask(radius=1.0, percent=60, threshold=2))
    return img


_ARROW = [(0, 0), (0, 10), (2.6, 7.6), (4.6, 11.6), (6.2, 10.9), (4.3, 7.0), (7.6, 7.0)]


def draw_cursor(img: Image.Image, x: float, y: float, scale: float = 1.0) -> None:
    """Small white arrow with a black outline (screen captures contain no cursor)."""
    if not (-12 <= x <= img.width + 12 and -12 <= y <= img.height + 12):
        return
    pts = [(x + px * scale, y + py * scale) for px, py in _ARROW]
    d = ImageDraw.Draw(img)
    d.polygon(pts, fill=(255, 255, 255), outline=(0, 0, 0))


def process_frame(src: Image.Image, size: Tuple[int, int], opts: ImageOptions,
                  cursor: Optional[Tuple[float, float]] = None) -> Image.Image:
    """Full pipeline: fit -> cursor -> colour -> rotation.
    For 90°/270° we fit to the swapped size so the final size is right."""
    w, h = size
    rot = opts.rotation % 360
    inner = (h, w) if rot in (90, 270) else (w, h)
    img, tf = fit_image(src, inner, opts.fit_mode, opts.background, opts.high_quality)
    if cursor is not None and opts.show_cursor:
        cx, cy = tf.map(*cursor)
        draw_cursor(img, cx, cy, scale=max(0.8, min(inner) / 128))
    img = enhance(img, opts.contrast, opts.saturation, opts.sharpen)
    return apply_orientation(img, rot, opts.flip_h)


def mean_difference(a: Optional[Image.Image], b: Optional[Image.Image]) -> float:
    """Mean difference 0-255 (change detection)."""
    if a is None or b is None or a.size != b.size:
        return 255.0
    diff = ImageChops.difference(a, b)
    if diff.getbbox() is None:
        return 0.0
    return sum(ImageStat.Stat(diff).mean) / 3.0


# ---------------------------------------------------------------------------
# Fonts and test images
# ---------------------------------------------------------------------------

_FONT_CANDIDATES = {
    True: ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"),
    False: ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"),
}


@lru_cache(maxsize=32)
def get_font(size: int, bold: bool = True):
    fonts_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in _FONT_CANDIDATES[bold]:
        for path in (os.path.join(fonts_dir, name), name):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - Pillow < 10.1
        return ImageFont.load_default()


def _center_text(d: ImageDraw.ImageDraw, box, text: str, font, fill) -> None:
    x0, y0, x1, y1 = box
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    d.text((x0 + (x1 - x0 - (r - l)) / 2 - l, y0 + (y1 - y0 - (b - t)) / 2 - t), text, font=font, fill=fill)


def make_test_pattern(w: int, h: int, title: str = "TEST") -> Image.Image:
    """Colour bars, grid, corner markers (R/G/B/Y) and the size - shows at a
    glance whether the image fills the screen and is the right way round."""
    img = Image.new("RGB", (w, h), (12, 12, 16))
    d = ImageDraw.Draw(img)
    bars = [(255, 255, 255), (255, 220, 0), (0, 220, 255), (0, 200, 0),
            (230, 0, 230), (230, 0, 0), (0, 60, 255), (20, 20, 20)]
    bw = w / len(bars)
    top = int(h * 0.62)
    for i, c in enumerate(bars):
        d.rectangle([round(i * bw), top, round((i + 1) * bw) - 1, h - 1], fill=c)
    for gx in range(0, w, 16):
        d.line([(gx, 0), (gx, top - 1)], fill=(45, 45, 60))
    for gy in range(0, top, 16):
        d.line([(0, gy), (w - 1, gy)], fill=(45, 45, 60))
    d.rectangle([0, 0, w - 1, h - 1], outline=(255, 255, 255))
    m = max(6, w // 16)
    for (x, y), c in zip([(0, 0), (w - m, 0), (0, h - m), (w - m, h - m)],
                         [(255, 0, 0), (0, 255, 0), (0, 90, 255), (255, 230, 0)]):
        d.rectangle([x, y, x + m - 1, y + m - 1], fill=c)
    _center_text(d, (0, 2, w, top * 0.55), title, get_font(max(10, h // 7)), (255, 255, 255))
    _center_text(d, (0, top * 0.5, w, top), f"{w}x{h}", get_font(max(9, h // 10), False), (170, 200, 255))
    return img


def make_number_card(w: int, h: int, number: str, subtitle: str = "",
                     bg=(20, 60, 160)) -> Image.Image:
    """A big number - for repeated-update and latency tests."""
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    _center_text(d, (0, 0, w, h * 0.78), number, get_font(int(h * 0.55)), (255, 255, 255))
    if subtitle:
        _center_text(d, (0, h * 0.72, w, h), subtitle, get_font(max(9, h // 9), False), (255, 240, 150))
    d.rectangle([0, 0, w - 1, h - 1], outline=(255, 255, 255))
    return img


CARD_COLORS = [(20, 60, 160), (160, 30, 40), (20, 120, 60), (120, 40, 150), (170, 100, 0)]
