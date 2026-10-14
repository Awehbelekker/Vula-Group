"""
vula/commerce/promo.py — a picture promotion to the shop's customers on WhatsApp.

2026-10-13 (Ian, with Caught Online's broadcasts as the example: "modern, rich and fun"): the
owner says what's on offer — on WhatsApp ("promote the Round Drop-In Basin: 15% off this
weekend") or in Broadcast — and every customer who hasn't opted out gets one message: the
product's photo, "Hi Thandi! 15% off this weekend…", and a **Shop now** button that opens the
shop (click-tracked per customer). It goes out on the shop's approved promotion template
(wa_templates.promo_template), so it reaches customers outside WhatsApp's 24-hour window, through
the same broadcast path as every other campaign: audience, POPIA suppression, pacing and the
delivered/read/clicked counts.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional

MAX_TEXT = 500


def clean(text: str) -> str:
    """One line — Meta refuses a template parameter with newlines, tabs or long runs of spaces."""
    return re.sub(r"\s+", " ", text or "").strip()[:MAX_TEXT]


def template_name(tenant_id: str) -> str:
    from vula.commerce.job_config import template_name as _name
    from vula.commerce.wa_templates import PROMO_SUFFIX
    return _name(tenant_id, PROMO_SUFFIX)


async def template_status(tenant_id: str) -> str:
    """APPROVED / PENDING / REJECTED / MISSING — the promotion can only go out once approved."""
    from vula.commerce import wa_templates
    name = template_name(tenant_id)
    for t in await wa_templates.list_templates(tenant_id):
        if t.get("name") == name:
            return t.get("status") or "UNKNOWN"
    return "MISSING"


async def pick(tenant_id: str, product_hint: Optional[str] = None,
               product_id: Optional[str] = None) -> Dict[str, Any]:
    """The photo and the page the button opens: the named product's, else the shop's front page
    with its first product photo. {"product", "image_url", "target_url"}."""
    from vula.commerce import web_basket
    products = await web_basket.shop_products(tenant_id)
    match = None
    if product_id:
        match = next((p for p in products if str(p.get("id")) == str(product_id)), None)
    elif product_hint:
        want = product_hint.strip().lower()
        named = [p for p in products if want in (p.get("name") or "").lower()]
        match = min(named, key=lambda p: len(p.get("name") or "")) if named else None
    photo_from = match or next((p for p in products if p.get("image_url")), None)
    return {"product": (match or {}).get("name"),
            "image_url": web_basket.sized((photo_from or {}).get("image_url"), 800),
            "target_url": web_basket.shop_url(tenant_id)}


async def send(tenant_id: str, message: str, *, product_hint: Optional[str] = None,
               product_id: Optional[str] = None, image_url: Optional[str] = None,
               audience: Any = "all", test_phone: Optional[str] = None,
               dry_run: bool = True) -> Dict[str, Any]:
    """Preview (dry_run) or send the promotion. Returns the broadcast result plus what was used."""
    text = clean(message)
    if not text:
        return {"error": "What's the offer? e.g. \"15% off all basins this weekend\"."}
    chosen = await pick(tenant_id, product_hint, product_id)
    if product_hint and not chosen["product"] and not product_id:
        return {"error": f"I couldn't find a product called '{product_hint}' in the shop."}
    image = (image_url or "").strip() or chosen["image_url"]
    if not image:
        return {"error": "The promotion needs a picture — add a product photo first."}
    status = await template_status(tenant_id)
    out = {"template": template_name(tenant_id), "template_status": status, "text": text,
           "product": chosen["product"], "image_url": image, "target_url": chosen["target_url"]}
    if not dry_run and status != "APPROVED":
        return {**out, "error": (
            "The picture promotion template isn't approved by Meta yet "
            f"({status.lower()}) — Settings › WhatsApp templates › Submit standard templates, then "
            "it's usually approved within a day.")}
    from fastapi import HTTPException
    from vula.api.commerce import admin_send_broadcast
    try:
        res = await admin_send_broadcast(tenant_id, {
            "template_name": out["template"], "audience_filter": audience or "all",
            "name": f"Promotion: {text[:60]}", "header_image_url": image,
            "target_url": chosen["target_url"], "template_params": ["{name}", text],
            "test_phone": test_phone, "dry_run": dry_run})
    except HTTPException as exc:
        return {**out, "error": str(exc.detail)}
    return {**out, **res}
