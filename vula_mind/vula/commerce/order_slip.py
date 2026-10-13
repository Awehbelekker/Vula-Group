"""
vula/commerce/order_slip.py — the paid-order slip: a till-slip picture in the WhatsApp chat and
a receipt page behind a "View receipt" button.

2026-10-13 (Ian): "like the slip that prints out on the tap and pay — same function" and "modern,
rich and fun". Tap to Pay (vula/tap) already ends every payment with a slip message and a
receipt link; a WhatsApp order now ends the same way, the moment it's paid.

The receipt link is the same unguessable, stateless token as Tap to Pay's (vula/tap/receipt.py,
an HMAC over the order id) with its own secret, and shows only what the customer already knows:
the shop, their items, the amounts and the order number — no phone number or address.
"""
from __future__ import annotations

import hashlib
import io
import textwrap
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

_SAST = timezone(timedelta(hours=2))


def _secret() -> str:
    from config import settings
    pepper = getattr(settings, "tap_hash_pepper", "") or hashlib.sha256(
        ("tap:" + (getattr(settings, "supabase_service_role_key", "") or settings.supabase_service_key or "")).encode()
    ).hexdigest()
    return "order-slip:" + pepper


def token_for(order_id: str) -> str:
    from vula.tap.receipt import make_token
    return make_token(_secret(), str(order_id), 0)


def order_id_from(token: str) -> Optional[str]:
    from vula.tap.receipt import read_token
    parsed = read_token(_secret(), (token or "")[:300])
    return parsed[0] if parsed else None


def urls(order_id: str) -> Dict[str, str]:
    """{"page", "image"} — empty when the server has no public address to link to."""
    from config import settings
    base = (settings.public_base_url or "").rstrip("/")
    if not base:
        return {}
    tok = token_for(order_id)
    return {"page": f"{base}/receipt/{tok}", "image": f"{base}/receipt/{tok}/slip.png"}


def rands(cents: int) -> str:
    return f"R{cents / 100:,.2f}"


def slip_data(order: Dict[str, Any], shop: Dict[str, Any]) -> Dict[str, Any]:
    """The customer-safe receipt: shop, items, amounts, order number, when — nothing else."""
    items = []
    for it in order.get("commerce_order_items") or []:
        q = float(it.get("quantity") or 0)
        cents = int(it.get("total_cents") or round(q * int(it.get("unit_price_cents") or 0)))
        items.append({"qty": int(q) if q == int(q) else q, "name": it.get("product_name") or "Item",
                      "cents": cents})
    total = int(order.get("total_cents") or 0)
    vat_no = shop.get("vat_number") if shop.get("vat_registered") else None
    when = order.get("paid_at") or order.get("updated_at") or order.get("created_at")
    try:
        paid = datetime.fromisoformat(str(when).replace("Z", "+00:00")).astimezone(_SAST)
    except (TypeError, ValueError):
        paid = datetime.now(_SAST)
    return {
        "shop": shop.get("name") or "Shop", "accent": shop.get("accent") or "#2C5545",
        "order": order.get("display_id") or str(order.get("id"))[:8].upper(),
        "items": items,
        "subtotal_cents": int(order.get("subtotal_cents") or sum(i["cents"] for i in items)),
        "delivery_cents": int(order.get("delivery_cents") or 0),
        "discount_cents": int(order.get("discount_cents") or 0),
        "total_cents": total,
        "vat_number": vat_no,
        "vat_cents": (total * 15 + 57) // 115 if vat_no else None,
        "paid_at": paid.strftime("%d %b %Y, %H:%M"),
        "method": {"yoco": "Card (Yoco)", "payfast": "Card (PayFast)", "eft": "EFT",
                   "card": "Card", "cash": "Cash"}.get(str(order.get("payment_method") or "").lower(),
                                                        "Paid"),
    }


async def load(order_id: str) -> Optional[Dict[str, Any]]:
    """The slip for an order, or None when it doesn't exist or isn't paid."""
    from vula.commerce import accounting, service
    order = await service.get_order(order_id)
    if not order or order.get("status") not in ("paid", "processing", "packed", "dispatched",
                                                 "out_for_delivery", "delivered", "completed", "ready"):
        return None
    tid = order["tenant_id"]
    inv = await service.get_invoice_settings(tid) or {}
    from vula.api import tenants
    accent = inv.get("accent_color") or "#2C5545"
    shop = {"name": inv.get("trading_as") or inv.get("company_name") or tenants.display_name(tid),
            "accent": accent if accent.startswith("#") else f"#{accent}",
            "vat_registered": accounting.is_vat_registered(tid), "vat_number": inv.get("vat_number")}
    return slip_data(order, shop)


# ── The picture ────────────────────────────────────────────────────────────────────────────

def _font(size: int, bold: bool = False, mono: bool = False):
    from PIL import ImageFont
    name = "DejaVuSansMono" if mono else ("DejaVuSans-Bold" if bold else "DejaVuSans")
    for path in (f"{name}.ttf", f"/usr/share/fonts/truetype/dejavu/{name}.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def _hex(c: str) -> tuple:
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (44, 85, 69)


def render_png(d: Dict[str, Any]) -> bytes:
    """A till slip on the shop's colour: torn edges, a PAID stamp, items and totals."""
    from PIL import Image, ImageDraw, ImageFilter
    W, SW, PAD = 720, 560, 34
    accent = _hex(d["accent"])
    f_shop, f_big, f_txt = _font(34, bold=True), _font(30, bold=True), _font(21)
    f_mono, f_small = _font(20, mono=True), _font(17)

    rows: List[tuple] = []                     # (kind, left, right)
    for it in d["items"]:
        wrapped = textwrap.wrap(f"{it['qty']} × {it['name']}", 30) or [""]
        rows.append(("item", wrapped[0], rands(it["cents"])))
        rows += [("item", w, "") for w in wrapped[1:]]
    rows.append(("rule", "", ""))
    rows.append(("sum", "Subtotal", rands(d["subtotal_cents"])))
    if d["discount_cents"]:
        rows.append(("sum", "Discount", "-" + rands(d["discount_cents"])))
    rows.append(("sum", "Delivery", rands(d["delivery_cents"]) if d["delivery_cents"] else "Free"))
    rows.append(("total", "TOTAL", rands(d["total_cents"])))
    if d["vat_cents"] is not None:
        rows.append(("small", f"Incl. VAT (15%) {rands(d['vat_cents'])} · VAT no. {d['vat_number']}", ""))

    body_h = 250 + sum(46 if k == "total" else 22 if k == "rule" else 32 for k, _, _ in rows) + 215
    H = body_h + 120
    img = Image.new("RGB", (W, H), tuple(min(255, int(c * 0.25 + 255 * 0.75)) for c in accent))
    dr = ImageDraw.Draw(img)
    x0, y0, x1, y1 = (W - SW) // 2, 50, (W + SW) // 2, 50 + body_h

    # Shadow, then the paper with zig-zag (torn) top and bottom.
    tooth = 14
    def paper(dx, dy, fill):
        pts = [(x0 + dx, y0 + dy + tooth)]
        for i, x in enumerate(range(x0, x1 + 1, tooth)):
            pts.append((x + dx, y0 + dy + (0 if i % 2 else tooth)))
        pts += [(x1 + dx, y1 + dy - tooth)]
        for i, x in enumerate(range(x1, x0 - 1, -tooth)):
            pts.append((x + dx, y1 + dy - (0 if i % 2 else tooth)))
        dr.polygon(pts, fill=fill)
    shadow = Image.new("L", (W, H), 0)
    dr = ImageDraw.Draw(shadow)
    paper(4, 10, 90)
    shadow = shadow.filter(ImageFilter.GaussianBlur(9))
    img.paste(Image.new("RGB", (W, H), tuple(int(c * 0.35) for c in accent)), (0, 0), shadow)
    dr = ImageDraw.Draw(img)
    paper(0, 0, (255, 255, 255))

    ink, muted = (34, 34, 30), (120, 116, 108)
    cx, y = W // 2, y0 + 44
    dr.text((cx, y), d["shop"][:28], font=f_shop, fill=ink, anchor="mt")
    y += 52
    dr.text((cx, y), f"Order {d['order']}", font=f_txt, fill=muted, anchor="mt")
    y += 30
    dr.text((cx, y), d["paid_at"], font=f_small, fill=muted, anchor="mt")
    y += 40

    # The stamp — a rotated PAID in the shop's colour.
    stamp = Image.new("RGBA", (190, 82), (0, 0, 0, 0))
    sd = ImageDraw.Draw(stamp)
    sd.rounded_rectangle((3, 3, 187, 79), radius=14, outline=accent + (235,), width=5)
    sd.text((95, 41), "PAID ✓", font=_font(36, bold=True), fill=accent + (235,), anchor="mm")
    stamp = stamp.rotate(-8, expand=True, resample=Image.BICUBIC)

    def dash(yy):
        for x in range(x0 + PAD, x1 - PAD, 14):
            dr.line((x, yy, x + 7, yy), fill=(200, 196, 188), width=2)
    dash(y)
    y += 22
    for kind, left, right in rows:
        if kind == "rule":
            dash(y + 8)
            y += 22
            continue
        if kind == "total":
            y += 6
            dr.text((x0 + PAD, y), left, font=f_big, fill=ink)
            dr.text((x1 - PAD, y), right, font=f_big, fill=accent, anchor="ra")
            y += 40
            continue
        font = f_small if kind == "small" else f_mono if kind == "item" else f_txt
        dr.text((x0 + PAD, y), left, font=font, fill=muted if kind == "small" else ink)
        if right:
            dr.text((x1 - PAD, y), right, font=font, fill=ink, anchor="ra")
        y += 32
    y += 18
    dash(y)
    y += 26
    dr.text((x0 + PAD, y), "Paid with", font=f_small, fill=muted)
    dr.text((x1 - PAD, y), d["method"], font=f_small, fill=ink, anchor="ra")
    y += 44
    dr.text((cx, y), f"Thank you for shopping with {d['shop'][:24]}!", font=f_txt, fill=ink, anchor="mt")
    img.paste(stamp, (cx - stamp.width // 2, y + 40), stamp)
    dr.text((cx, H - 46), "Ordered on WhatsApp · powered by Vula", font=f_small,
            fill=tuple(int(c * 0.6) for c in accent), anchor="mt")
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
