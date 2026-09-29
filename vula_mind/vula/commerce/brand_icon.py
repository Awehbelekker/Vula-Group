"""
vula/commerce/brand_icon.py — a business's square app icon (PNG) for its installed phone app.

From its square icon if it uploaded one, else its logo centred on a white tile, else its initial
on its brand colour. iOS ignores SVG touch icons, so these are always PNG.
"""
from __future__ import annotations

import io
import logging
from typing import Optional

log = logging.getLogger(__name__)


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


async def render_icon(src: Optional[str], accent: str, name: str, size: int, square: bool = False) -> bytes:
    from PIL import Image
    img = None
    if src:
        try:
            from vula.commerce.reference_url import safe_fetch_bytes
            data = await safe_fetch_bytes(src)
            logo = Image.open(io.BytesIO(data)).convert("RGBA")
            if square:                       # an uploaded app icon: fill the tile
                img = logo.resize((size, size))
                bg = Image.new("RGB", (size, size), (255, 255, 255))
                bg.paste(img, mask=img.split()[3])
                img = bg
            else:                            # a wide logo: centred with a safe margin (maskable)
                tile = Image.new("RGB", (size, size), (255, 255, 255))
                inner = int(size * 0.7)
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
