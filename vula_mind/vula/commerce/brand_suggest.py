"""
vula/commerce/brand_suggest.py — a brand kit suggested from a business's own logo or website.

2026-09-29 (Ian: "customise to tenant… one tap"). DIGG uploaded its logo but its brand kit stayed
on Vula's default green; nobody should have to know hex codes. The colours are SAMPLED from the
real image (pixel quantisation, not a model's guess), in the brand's own hue — unlike
page_copy.suggest_theme, which snaps to a mood preset's shortlist for storefront pages. A website
contributes its declared theme-color and its logo/icon. Nothing is saved: the Brand kit shows the
suggestion as a preview and the owner taps Apply.
"""
from __future__ import annotations

import io
import logging
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urljoin

log = logging.getLogger(__name__)

_HEX = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


def _hex(rgb: Tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def _rgb(hex_: str) -> Tuple[int, int, int]:
    h = hex_.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _lum(rgb) -> float:
    def ch(c):
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_lum(_rgb(a)), _lum(_rgb(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _dist(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def palette(img, n: int = 3) -> Dict[str, Any]:
    """Distinct brand colours in an image, most used first, plus the darkest real colour (a
    charcoal/black logo has no saturated colour at all — that darkness IS its brand)."""
    rgba = img.convert("RGBA").resize((64, 64))
    colourful: Counter = Counter()
    dark: Counter = Counter()
    for r, g, b, a in rgba.getdata():
        if a < 128:                                   # transparent background
            continue
        mx, mn = max(r, g, b), min(r, g, b)
        if mx > 235 and mn > 200:                     # white/background
            continue
        key = (r // 12 * 12, g // 12 * 12, b // 12 * 12)
        if mx - mn >= 28 and mx >= 40:
            colourful[key] += 1
        elif mx < 110:
            dark[key] += 1
    picks: List[Tuple[int, int, int]] = []
    for c, _ in colourful.most_common(40):
        if all(_dist(c, p) > 60 for p in picks):
            picks.append(c)
        if len(picks) >= n:
            break
    darkest = min(dark, key=lambda c: sum(c)) if dark else None
    return {"colours": [_hex(c) for c in picks], "dark": _hex(darkest) if darkest else None}


def _readable_accent(hex_: str) -> str:
    """Darken a light brand colour until white button text reads on it (≥ 3:1, large-text AA) —
    a pale logo teal (#60D0DE) would otherwise give unreadable buttons."""
    r, g, b = _rgb(hex_)
    for _ in range(12):
        if contrast(_hex((r, g, b)), "#FFFFFF") >= 3.0:
            break
        r, g, b = int(r * 0.88), int(g * 0.88), int(b * 0.88)
    return _hex((r, g, b))


def suggest_from_image(img, theme_color: Optional[str] = None) -> Dict[str, Any]:
    pal = palette(img)
    colours = list(pal["colours"])
    if theme_color and _HEX.match(theme_color or ""):
        colours.insert(0, "#" + theme_color.lstrip("#").upper())
    if colours:
        accent = _readable_accent(colours[0])
        secondary = colours[1] if len(colours) > 1 else None
        ink = pal["dark"] if pal["dark"] and _lum(_rgb(pal["dark"])) < 0.05 else "#1E1E1E"
        why = "sampled from the colours in your logo"
    elif pal["dark"]:
        # A monochrome logo (DIGG's charcoal): the brand is the dark colour itself — charcoal
        # accent and ink, a warm stone secondary for highlights.
        accent, secondary, ink = pal["dark"], "#8C7B6B", pal["dark"]
        why = "your logo is monochrome, so the brand is its own dark tone"
    else:
        return {}
    return {"accent_color": accent, "secondary_color": secondary, "ink_color": ink,
            "palette": colours[:4], "why": why}


def _find_logo_urls(html: str, base: str) -> Tuple[List[str], Optional[str]]:
    """A page's own logo/icon candidates (best first) and its declared theme-color."""
    theme = None
    m = re.search(r'<meta[^>]+name=["\']theme-color["\'][^>]*content=["\']([^"\']+)', html, re.I) \
        or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]*name=["\']theme-color', html, re.I)
    if m and _HEX.match(m.group(1).strip()):
        theme = m.group(1).strip()
    urls: List[str] = []
    for pat in (r'<img[^>]+(?:class|id|alt|src)=["\'][^"\']*logo[^"\']*["\'][^>]*>',
                r'<link[^>]+rel=["\'][^"\']*apple-touch-icon[^"\']*["\'][^>]*>',
                r'<meta[^>]+property=["\']og:image["\'][^>]*>',
                r'<link[^>]+rel=["\'][^"\']*icon[^"\']*["\'][^>]*>'):
        for tag in re.findall(pat, html, re.I):
            src = re.search(r'(?:src|href|content)=["\']([^"\']+)', tag, re.I)
            if src and not src.group(1).startswith("data:"):
                u = urljoin(base, src.group(1))
                if u not in urls:
                    urls.append(u)
    return urls[:5], theme


async def suggest(logo_url: Optional[str] = None, website_url: Optional[str] = None) -> Dict[str, Any]:
    """A brand kit suggestion from a logo image and/or a website. Never raises: {} when nothing
    usable was found (the caller says so)."""
    from PIL import Image
    from vula.commerce.reference_url import safe_fetch_bytes, safe_fetch_html
    theme = None
    candidates: List[str] = []
    if website_url:
        url = website_url if re.match(r"^https?://", website_url) else f"https://{website_url}"
        try:
            html = await safe_fetch_html(url)
            candidates, theme = _find_logo_urls(html, url)
        except Exception as exc:
            log.info("brand suggest: website %s unreadable: %s", url, exc)
    if logo_url:
        candidates.insert(0, logo_url)
    for u in candidates:
        try:
            data = await safe_fetch_bytes(u)
            img = Image.open(io.BytesIO(data))
            img.load()
        except Exception as exc:
            log.debug("brand suggest: image %s unusable: %s", u, exc)
            continue
        out = suggest_from_image(img, theme)
        if out:
            out["source"] = u
            if website_url and u != logo_url:
                out["logo_url"] = u
            return out
    if theme:
        return {"accent_color": _readable_accent(theme), "secondary_color": None, "ink_color": "#1E1E1E",
                "palette": [theme], "why": "your website's own theme colour", "source": website_url}
    return {}
