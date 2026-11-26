"""Generate the icon, installer artwork and the README banner.

    python tools/make_art.py
"""

import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
ASSETS = os.path.join(ROOT, "assets")
INSTALLER = os.path.join(ROOT, "installer")
DOCS = os.path.join(ROOT, "docs")

BG_TOP = (36, 40, 52)
BG_BOTTOM = (16, 17, 21)
ACCENT = (76, 125, 255)


def font(size, bold=True):
    win = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
    for name in (("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")):
        try:
            return ImageFont.truetype(os.path.join(win, name), size)
        except OSError:
            pass
    return ImageFont.load_default()


def gradient(w, h, top=BG_TOP, bottom=BG_BOTTOM):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return img


def device(size: int) -> Image.Image:
    """The app icon: a small speaker with a screen."""
    s = size / 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([16 * s, 24 * s, 240 * s, 232 * s], radius=44 * s, fill=(34, 38, 50, 255),
                        outline=ACCENT + (255,), width=max(1, round(8 * s)))
    d.rounded_rectangle([46 * s, 50 * s, 210 * s, 182 * s], radius=10 * s, fill=(8, 10, 16, 255))
    bars = [(76, 125, 255), (51, 194, 127), (242, 184, 75), (229, 83, 75)]
    bw = (150 * s) / len(bars)
    for i, c in enumerate(bars):
        d.rectangle([53 * s + i * bw, 120 * s, 53 * s + (i + 1) * bw - 1, 174 * s], fill=c + (255,))
    d.rectangle([53 * s, 58 * s, 203 * s, 116 * s], fill=(28, 46, 90, 255))
    d.polygon([(110 * s, 72 * s), (110 * s, 104 * s), (138 * s, 88 * s)], fill=(255, 255, 255, 255))
    for x in range(5):
        cx = (70 + x * 29) * s
        d.ellipse([cx - 6 * s, 200 * s, cx + 6 * s, 212 * s], fill=(120, 130, 150, 255))
    return img


def glow(base: Image.Image, icon: Image.Image, xy, radius=24):
    halo = Image.new("RGBA", base.size, (0, 0, 0, 0))
    mask = icon.split()[3].point(lambda a: 120 if a else 0)
    tint = Image.new("RGBA", icon.size, ACCENT + (255,))
    halo.paste(tint, xy, mask)
    halo = halo.filter(ImageFilter.GaussianBlur(radius))
    base.alpha_composite(halo)
    base.alpha_composite(icon, xy)


def wizard_large():
    w, h = 410, 797                      # Inno Setup modern wizard, 250 % scale
    img = gradient(w, h).convert("RGBA")
    ic = device(250)
    glow(img, ic, ((w - 250) // 2, 150), 30)
    d = ImageDraw.Draw(img)
    for text, y, f, c in (("MiniToo", 450, font(64), (255, 255, 255)),
                          ("Mirror", 520, font(64), ACCENT),
                          ("for the Divoom MiniToo", 610, font(26, False), (170, 178, 192))):
        l, t, r, b = d.textbbox((0, 0), text, font=f)
        d.text(((w - (r - l)) / 2 - l, y), text, font=f, fill=c)
    return img.convert("RGB")


def wizard_small():
    img = gradient(138, 140, (44, 48, 60), (30, 32, 40)).convert("RGBA")
    ic = device(116)
    img.alpha_composite(ic, (11, 12))
    return img.convert("RGB")


def banner():
    w, h = 1280, 400
    img = gradient(w, h, (30, 33, 43), (15, 16, 20)).convert("RGBA")
    ic = device(260)
    glow(img, ic, (90, 70), 34)
    d = ImageDraw.Draw(img)
    d.text((410, 92), "MiniToo Mirror", font=font(92), fill=(255, 255, 255))
    d.text((414, 214), "Your Windows screen, windows, images and videos", font=font(36, False),
           fill=(190, 198, 212))
    d.text((414, 262), "on the Divoom MiniToo display — over Bluetooth", font=font(36, False),
           fill=(190, 198, 212))
    d.rounded_rectangle([414, 322, 640, 362], radius=20, fill=ACCENT)
    d.text((436, 327), "14 languages", font=font(24), fill=(255, 255, 255))
    d.rounded_rectangle([656, 322, 900, 362], radius=20, outline=ACCENT, width=2)
    d.text((678, 327), "Windows 10 / 11", font=font(24), fill=(220, 226, 236))
    return img.convert("RGB")


if __name__ == "__main__":
    for p in (ASSETS, INSTALLER, DOCS):
        os.makedirs(p, exist_ok=True)
    big = device(256)
    big.save(os.path.join(ASSETS, "icon.ico"),
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    big.save(os.path.join(ASSETS, "icon.png"))
    wizard_large().save(os.path.join(INSTALLER, "wizard-large.bmp"))
    wizard_small().save(os.path.join(INSTALLER, "wizard-small.bmp"))
    banner().save(os.path.join(DOCS, "banner.png"))
    print("art written")
