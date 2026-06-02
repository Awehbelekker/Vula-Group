#!/usr/bin/env python
"""
test_smart_scanner.py — standalone end-to-end test for the AI Smart Scanner.

Generates a realistic sample supplier receipt as an image, sends it through the
live /admin/scan endpoint, and prints what the vision LLM extracted. Then runs
/admin/scan/commit in PREVIEW mode (auto_commit=false) so nothing is written to
the books — it just shows what would be booked, the resolved due date, etc.

This confirms the OpenRouter vision extraction works end-to-end with one real
API call (a few cents of credit) and no manual photographing.

Usage:
    python scripts/test_smart_scanner.py                 # hits Railway prod
    python scripts/test_smart_scanner.py --local         # hits localhost:8000
    python scripts/test_smart_scanner.py --image path.jpg  # use your own image
    python scripts/test_smart_scanner.py --commit        # actually write to books

Env:
    VULA_API_KEY   — sent as X-API-Key (read from .env if present)
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from io import BytesIO

import httpx

PROD_URL = "https://vula-group-production.up.railway.app"
LOCAL_URL = "http://localhost:8000"
TENANT = "off-the-hook"


def make_sample_receipt() -> bytes:
    """Render a fake but realistic SA supplier receipt to PNG bytes via PIL."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        sys.exit("Pillow not installed. Run: pip install Pillow  (or pass --image <file>)")

    W, H = 520, 640
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    def font(size: int):
        for name in ("arial.ttf", "DejaVuSans.ttf", "DejaVuSans-Bold.ttf"):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    big, mid, small = font(26), font(17), font(14)

    lines = [
        (20, big,   "OCEAN FRESH SUPPLIERS CC"),
        (54, small, "12 Harbour Rd, Cape Town  |  VAT 4123456789"),
        (74, small, "Tel 021 555 0182"),
        (104, mid,  "TAX INVOICE  #OFS-20418"),
        (130, small, "Date: 2026-05-28        Terms: 30 days"),
        (150, small, "Bill to: Off the Hook"),
    ]
    for y, f, t in lines:
        d.text((20, y), t, fill="black", font=f)

    d.line((20, 178, W - 20, 178), fill="black", width=1)
    d.text((20, 186), "Item", fill="black", font=small)
    d.text((300, 186), "Qty", fill="black", font=small)
    d.text((400, 186), "Total", fill="black", font=small)
    d.line((20, 206, W - 20, 206), fill="black", width=1)

    items = [
        ("Norwegian Salmon (whole)", "8 kg", "R2 240.00"),
        ("Hake fillets",            "15 kg", "R1 425.00"),
        ("Prawns 16/20 (frozen)",   "5 kg",  "R1 100.00"),
        ("Calamari tubes",          "6 kg",  "R 690.00"),
        ("Ice (food grade)",        "20 kg", "R 120.00"),
    ]
    y = 216
    for name, qty, total in items:
        d.text((20, y), name, fill="black", font=small)
        d.text((300, y), qty, fill="black", font=small)
        d.text((400, y), total, fill="black", font=small)
        y += 26

    y += 8
    d.line((300, y, W - 20, y), fill="black", width=1)
    y += 8
    d.text((300, y), "Subtotal", fill="black", font=small); d.text((400, y), "R5 365.22", fill="black", font=small)
    y += 24
    d.text((300, y), "VAT 15%", fill="black", font=small);  d.text((400, y), "R 804.78", fill="black", font=small)
    y += 26
    d.text((300, y), "TOTAL", fill="black", font=mid);      d.text((400, y), "R6 170.00", fill="black", font=mid)

    d.text((20, H - 40), "Thank you for your business!", fill="black", font=small)

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--local", action="store_true", help="hit localhost instead of prod")
    ap.add_argument("--image", help="path to your own receipt image instead of the generated one")
    ap.add_argument("--commit", action="store_true", help="actually write to the books (default: preview only)")
    ap.add_argument("--tenant", default=TENANT)
    args = ap.parse_args()

    # Windows consoles default to cp1252 — force UTF-8 so arrows/emoji print.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    base = LOCAL_URL if args.local else PROD_URL
    api_key = os.environ.get("VULA_API_KEY", "")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    if args.image:
        img_bytes = open(args.image, "rb").read()
        print(f"Using image: {args.image} ({len(img_bytes)} bytes)")
    else:
        img_bytes = make_sample_receipt()
        print(f"Generated sample receipt ({len(img_bytes)} bytes) — Ocean Fresh Suppliers, R6 170.00")

    img_b64 = base64.b64encode(img_bytes).decode()

    # ── 1. Scan ──────────────────────────────────────────────────────────────
    print(f"\n→ POST {base}/v1/commerce/{args.tenant}/admin/scan  (vision LLM)…")
    with httpx.Client(timeout=90.0) as c:
        r = c.post(
            f"{base}/v1/commerce/{args.tenant}/admin/scan",
            headers=headers,
            json={"image_base64": img_b64, "doc_type": "auto"},
        )
    print(f"   HTTP {r.status_code}")
    if r.status_code != 200:
        print("   FAILED:", r.text[:500])
        sys.exit(1)

    data = r.json()
    extracted = data.get("extracted", {})
    esc = " (escalated local→cloud)" if data.get("escalated") else ""
    print(f"   model: {data.get('model')}{esc}")
    print("\n── EXTRACTED ──────────────────────────────────────────────")
    print(json.dumps(extracted, indent=2, ensure_ascii=False))

    # ── Quick sanity scorecard ────────────────────────────────────────────────
    if not args.image:
        print("\n── ACCURACY (vs known sample) ─────────────────────────────")
        checks = [
            ("supplier contains 'Ocean Fresh'", "ocean fresh" in (extracted.get("supplier") or "").lower()),
            ("doc_type is invoice/receipt", extracted.get("doc_type") in ("invoice", "receipt")),
            ("total ≈ R6170 (617000c)", abs((extracted.get("total_cents") or 0) - 617000) <= 1000),
            ("line_items >= 4 found", len(extracted.get("line_items") or []) >= 4),
        ]
        for label, ok in checks:
            print(f"   {'✅' if ok else '❌'} {label}")

    # ── 2. Commit (preview unless --commit) ───────────────────────────────────
    mode = "COMMIT (writes to books!)" if args.commit else "PREVIEW (no write)"
    print(f"\n→ POST {base}/v1/commerce/{args.tenant}/admin/scan/commit  [{mode}]…")
    with httpx.Client(timeout=60.0) as c:
        r = c.post(
            f"{base}/v1/commerce/{args.tenant}/admin/scan/commit",
            headers=headers,
            json={"extracted": extracted, "auto_commit": args.commit},
        )
    print(f"   HTTP {r.status_code}")
    if r.status_code == 200:
        print(json.dumps(r.json(), indent=2, ensure_ascii=False))
    else:
        print("   ", r.text[:500])

    print("\nDone.")


if __name__ == "__main__":
    main()
