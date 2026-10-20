"""
vula/commerce/powered_by.py — the "Powered by Vula" credit on what a tenant's customers see.

From the Vula logo set, batch 2 (vula/commerce/assets/, files as supplied): the pill for web pages
(the shop page, the pay page), the inline badge for PDFs (invoices, quotes). Usage rules from that
set: the tenant's own logo stays top-left; Vula appears only as this small credit at the foot;
never below 90 px wide; one badge-height of clear space around it.

The pill's light and dark versions are the same artwork in different colours, so the page carries
one inline copy whose fills are classes, and dark mode swaps the colours with CSS.
"""
from __future__ import annotations

import base64
import re
from functools import lru_cache
from pathlib import Path

VULA_URL = "https://vula-ai.com"
_ASSETS = Path(__file__).parent / "assets"

# Light and dark fills per part, from vula-powered-by-pill-light.svg / -dark.svg.
# The artwork's parts, in document order: pill, "Powered by", mark, dot, wordmark.
_PARTS = ("bg", "txt", "mark", "dot", "word")
_LIGHT = {"bg": "#F6F4EF", "edge": "#D9D4C7", "txt": "#5E625C", "mark": "#1F5A45",
          "dot": "#E8A93A", "word": "#1F5A45"}
_DARK = {"bg": "#1B1E1B", "edge": "#33433B", "txt": "#A3A8A0", "mark": "#5FBF95",
         "dot": "#F2BE5C", "word": "#F1F1EC"}


def _rules(colours: dict) -> str:
    return (f".vpb-bg{{fill:{colours['bg']};stroke:{colours['edge']}}}"
            + "".join(f".vpb-{p}{{fill:{colours[p]}}}" for p in _PARTS[1:]))


@lru_cache(maxsize=1)
def _pill_svg() -> str:
    """The pill as inline markup: fills become classes, the title goes (the link names it)."""
    svg = (_ASSETS / "vula-powered-by-pill-light.svg").read_text(encoding="utf-8")
    svg = re.sub(r"<title>.*?</title>", "", svg)
    svg = re.sub(r' width="[\d.]+" height="[\d.]+"', "", svg, count=1)
    svg = svg.replace("<svg ", '<svg aria-hidden="true" focusable="false" ', 1)
    parts = iter(_PARTS)
    svg = re.sub(r' fill="#[0-9A-Fa-f]{6}"( stroke="#[0-9A-Fa-f]{6}")?',
                 lambda _m: f' class="vpb-{next(parts)}"', svg)
    return svg


def pill_css(dark_mode: bool = True) -> str:
    """CSS for the pill. With dark_mode, it follows the visitor's light / dark setting."""
    css = (".vpb{display:flex;justify-content:center;padding:32px 16px 24px}"
           ".vpb a{display:inline-block;line-height:0;border-radius:999px}"
           ".vpb a:focus-visible{outline:2px solid currentColor;outline-offset:3px}"
           ".vpb svg{height:34px;width:auto}" + _rules(_LIGHT))
    if dark_mode:
        css += "@media (prefers-color-scheme:dark){" + _rules(_DARK) + "}"
    return css


def pill_html() -> str:
    """The footer credit: the pill, linking to Vula's site. Pair with pill_css()."""
    return (f'<footer class="vpb"><a href="{VULA_URL}" target="_blank" rel="noopener" '
            f'aria-label="Powered by Vula">{_pill_svg()}</a></footer>')


@lru_cache(maxsize=1)
def inline_badge_data_uri() -> str:
    """The inline badge (colour, transparent) as a data: URI, for WeasyPrint PDFs."""
    raw = (_ASSETS / "vula-powered-by-inline-color.svg").read_bytes()
    return "data:image/svg+xml;base64," + base64.b64encode(raw).decode("ascii")
