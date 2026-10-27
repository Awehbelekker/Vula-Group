"""
vula/commerce/brand_icon.py — a business's square app icon (PNG) for its installed phone app.

From its square icon if it uploaded one, else its logo centred on a white tile, else its initial
on its brand colour. iOS ignores SVG touch icons, so these are always PNG.

maskable (2026-10-27): Android shows an installed app's icon as a circle or squircle and keeps only
the middle — a circle 80% of the icon wide. The maskable variant keeps everything that matters
inside that circle (a logo within MASK_SAFE of the tile; a square icon shrunk onto its own edge
colour), so no corner of a business's logo is cut off. The plain icon is unchanged.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

log = logging.getLogger(__name__)

# The widest a square can be and still sit inside the 80%-wide safe circle: 0.8 / √2 ≈ 0.566.
MASK_SAFE = 0.56


def _hex_rgb(h: str):
    h = (h or "#2C5545").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (44, 85, 69)


def _letter_tile(accent: str, name: str, size: int):
    from PIL import Image, ImageDraw, ImageFont
    rgb = _hex_rgb(accent)
    img = Image.new("RGB", (size, size), rgb)
    d = ImageDraw.Draw(img)
    letter = (name or "V").strip()[:1].upper() or "V"
    lum = (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255
    fg = (30, 30, 30) if lum > 0.62 else (255, 255, 255)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", int(size * 0.5))
    except Exception:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), letter, font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((size - w) / 2 - box[0], (size - h) / 2 - box[1]), letter, fill=fg, font=font)
    return img


def _edge_colour(img) -> tuple:
    """The colour around a square icon's edge (its four corners, averaged)."""
    w, h = img.size
    px = [img.getpixel((x, y)) for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
    solid = [p for p in px if len(p) < 4 or p[3] > 32] or [(255, 255, 255, 255)]
    return tuple(sum(p[i] for p in solid) // len(solid) for i in range(3))


async def render_icon(src: Optional[str], accent: str, name: str, size: int, square: bool = False,
                      logo_size: Optional[str] = None, maskable: bool = False) -> bytes:
    from PIL import Image
    img = None
    if src:
        try:
            from vula.commerce.reference_url import safe_fetch_bytes
            data = await safe_fetch_bytes(src)
            logo = Image.open(io.BytesIO(data)).convert("RGBA")
            if square and maskable:          # an uploaded app icon, shrunk onto its own edge colour
                inner = int(size * 0.8)
                shrunk = logo.resize((inner, inner))
                bg = Image.new("RGB", (size, size), _edge_colour(logo))
                bg.paste(shrunk, ((size - inner) // 2, (size - inner) // 2), mask=shrunk.split()[3])
                img = bg
            elif square:                     # an uploaded app icon: fill the tile
                img = logo.resize((size, size))
                bg = Image.new("RGB", (size, size), (255, 255, 255))
                bg.paste(img, mask=img.split()[3])
                img = bg
            else:                            # a wide logo: centred with a safe margin (maskable)
                tile = Image.new("RGB", (size, size), (255, 255, 255))
                from vula.commerce.brand_sizes import ICON_FILL, size_key
                fill = ICON_FILL[size_key(logo_size)]
                inner = int(size * (min(fill, MASK_SAFE) if maskable else fill))
                logo.thumbnail((inner, inner))
                tile.paste(logo, ((size - logo.width) // 2, (size - logo.height) // 2), mask=logo.split()[3])
                img = tile
        except Exception as exc:
            log.debug("app icon from %s failed, using a letter tile: %s", src, exc)
    if img is None:
        img = _letter_tile(accent, name, size)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()


# ── The iPhone's launch screen (2026-10-27, PWA pass 6) ───────────────────────
# iOS shows a startup image while an installed web app opens — a blank white screen without one —
# and only one made for the exact screen size. Portrait, every iPhone still in use (CSS width x
# height @ pixel ratio), as the dashboard links them (src/lib/iosSplash.js). Anything else is
# refused rather than rendered (no one-off sizes to cache or burn CPU on).
SPLASH_SIZES = {
    (1320, 2868), (1290, 2796), (1206, 2622), (1179, 2556), (1284, 2778), (1170, 2532),
    (1125, 2436), (1242, 2688), (828, 1792), (1242, 2208), (750, 1334), (640, 1136),
}
# The dashboard's own page colours (src/index.css --bg / --ink, light and dark).
SPLASH_LOOK = {False: ((247, 244, 238), (30, 30, 30)), True: ((18, 18, 17), (242, 239, 232))}


def _rounded(img, radius: int):
    from PIL import Image, ImageDraw
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.size[0] - 1, img.size[1] - 1), radius, fill=255)
    out = Image.new("RGBA", img.size)
    out.paste(img.convert("RGBA"), mask=mask)
    return out


def _fit(name: str, font_for, max_w: int, size: int):
    """The name at the largest size up to `size` that fits `max_w`, else shortened with an ellipsis."""
    for s in range(size, max(size // 2, 8) - 1, -2):
        font = font_for(s)
        if font.getlength(name) <= max_w:
            return name, font
    font = font_for(max(size // 2, 8))
    while name and font.getlength(name + "…") > max_w:
        name = name[:-1]
    return name.rstrip() + "…", font


async def render_splash(src: Optional[str], accent: str, name: str, width: int, height: int,
                        square: bool = False, logo_size: Optional[str] = None, dark: bool = False) -> bytes:
    """The business's app icon (rounded, as on the home screen) above its name, on the
    dashboard's page colour — so opening the app looks like the app."""
    from PIL import Image, ImageDraw, ImageFont
    bg, ink = SPLASH_LOOK[bool(dark)]
    img = Image.new("RGB", (width, height), bg)
    side = int(min(width, height) * 0.30)
    icon = Image.open(io.BytesIO(await render_icon(src, accent, name, side, square=square, logo_size=logo_size)))
    icon = _rounded(icon, int(side * 0.225))
    top = int(height * 0.42) - side // 2
    img.paste(icon, ((width - side) // 2, top), mask=icon.split()[3])

    def font_for(s):
        try:
            return ImageFont.truetype("DejaVuSans-Bold.ttf", s)
        except Exception:
            return ImageFont.load_default()
    label, font = _fit((name or "").strip(), font_for, int(width * 0.84), int(width * 0.06))
    if label:
        d = ImageDraw.Draw(img)
        box = d.textbbox((0, 0), label, font=font)
        d.text(((width - (box[2] - box[0])) / 2 - box[0], top + side + int(side * 0.18)), label, fill=ink, font=font)
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
