"""
vula/commerce/image_prep.py — flatten a photographed receipt/invoice into something that reads
like a flat scan/photocopy, using the corner points the receipt-scan vision call already returns
(2026-08-27, whatsapp.py::_scan_financial_photo) — no extra vision call, no numpy/OpenCV
dependency (Pillow's own QUAD transform maps a source quadrilateral straight to a rectangle).

Best-effort throughout: a bad/missing/degenerate corner detection returns the ORIGINAL bytes
unchanged — a slightly-angled but perfectly readable photo is always safer than a garbled crop.
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

_MIN_ASPECT = 0.15  # a detected quad this thin/degenerate is almost certainly a bad read
_MAX_ASPECT = 6.0
_MIN_SIDE_PX = 50


def _valid_corners(corners) -> Optional[List[Tuple[float, float]]]:
    """Whitelist-validate the vision model's corner output — 4 points, each an [x,y] pair of
    floats in [0,1] — never trust it blindly. Returns None (skip flattening) on anything
    malformed or out of range."""
    try:
        if not isinstance(corners, list) or len(corners) != 4:
            return None
        pts = []
        for p in corners:
            if not isinstance(p, (list, tuple)) or len(p) != 2:
                return None
            x, y = float(p[0]), float(p[1])
            if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
                return None
            pts.append((x, y))
        return pts
    except (TypeError, ValueError):
        return None


def flatten_receipt(image_bytes: bytes, corners) -> bytes:
    """Perspective-warp the detected receipt quad flat, then auto-contrast/sharpen it toward a
    photocopy look. Falls back to the original bytes on any failure — corners missing/invalid,
    a geometrically implausible quad, or a Pillow error."""
    pts = _valid_corners(corners)
    if pts is None:
        return image_bytes
    try:
        import io

        from PIL import Image, ImageFilter, ImageOps

        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)  # respect the phone's own orientation metadata first
        w, h = img.size
        # Vision model returns corners in reading order (top-left, top-right, bottom-right,
        # bottom-left) — PIL's QUAD transform wants (top-left, bottom-left, bottom-right,
        # top-right), so re-order once here rather than asking the prompt to match PIL's own
        # unusual convention.
        tl, tr, br, bl = [(x * w, y * h) for x, y in pts]
        quad = (tl[0], tl[1], bl[0], bl[1], br[0], br[1], tr[0], tr[1])

        def dist(a: Tuple[float, float], b: Tuple[float, float]) -> float:
            return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

        out_w = int(max(dist(tl, tr), dist(bl, br)))
        out_h = int(max(dist(tl, bl), dist(tr, br)))
        if out_w < _MIN_SIDE_PX or out_h < _MIN_SIDE_PX:
            return image_bytes
        aspect = out_w / out_h
        if not (_MIN_ASPECT <= aspect <= _MAX_ASPECT):
            return image_bytes

        flat = img.convert("RGB").transform((out_w, out_h), Image.QUAD, quad, Image.BICUBIC)
        flat = ImageOps.autocontrast(flat, cutoff=1)
        flat = flat.filter(ImageFilter.SHARPEN)

        buf = io.BytesIO()
        flat.save(buf, format="JPEG", quality=90)
        return buf.getvalue()
    except Exception as exc:
        log.debug("receipt flatten skipped: %s", exc)
        return image_bytes
