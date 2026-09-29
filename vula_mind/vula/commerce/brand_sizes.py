"""
vula/commerce/brand_sizes.py — how big the business's logo is on each surface, from ONE setting.

2026-09-29 (Ian: "logo size adjustment on all factors needs work"). The brand kit's logo_size
(sm/md/lg, now also xl) only reached invoice PDFs; the dashboard, login, emails, the WhatsApp
menu page and the phone app icon each used their own fixed size. Every surface reads this table
so one choice shows everywhere, in proportion. Heights in px (max widths keep a wide logo sane);
`icon` is the share of the square app-icon tile the logo fills.
"""
from __future__ import annotations

LOGO_SIZES = ("sm", "md", "lg", "xl")

LOGO_PX = {
    "pdf":   {"sm": (56, 180), "md": (76, 240), "lg": (100, 320), "xl": (124, 380)},
    "email": {"sm": (36, 160), "md": (48, 220), "lg": (60, 260), "xl": (72, 300)},
    "menu":  {"sm": (32, 140), "md": (40, 180), "lg": (52, 220), "xl": (64, 260)},
}
ICON_FILL = {"sm": 0.58, "md": 0.7, "lg": 0.8, "xl": 0.88}


def size_key(value) -> str:
    return value if value in LOGO_SIZES else "md"


def logo_px(surface: str, value) -> tuple[int, int]:
    """(max_height, max_width) for a surface at the business's chosen size."""
    return LOGO_PX[surface][size_key(value)]
