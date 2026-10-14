"""
vula/commerce/wa_profile.py — the shop's WhatsApp business profile, set from its brand kit.

2026-10-13 (the WhatsApp shop plan, Ian: "let Vula stand out"; "streamline it for the user"):
the profile a customer sees when they tap the shop's name in WhatsApp — picture, about line,
description, email, websites, address, category — used to be typed by hand in Meta's WhatsApp
Manager, if at all. One tap in Settings now proposes it from what Vula already knows (the brand
kit and invoice details, the business description, the shop's own address) and, on Apply, sets
it on the shop's WhatsApp number through Meta's Cloud API
(POST /{phone_number_id}/whatsapp_business_profile). Nothing is changed without the Apply tap;
what Meta holds afterwards is read back and shown.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import httpx

log = logging.getLogger(__name__)

GRAPH = "https://graph.facebook.com/v19.0"
FIELDS = "about,address,description,email,profile_picture_url,websites,vertical"
# Meta's categories, from the business type Vula knows.
VERTICALS = {"food": "GROCERY", "retail": "RETAIL", "services": "PROF_SERVICES", "trades": "OTHER",
             "rep": "OTHER", "beauty": "BEAUTY", "health": "HEALTH", "community": "NONPROFIT",
             "education": "EDU", "hospitality": "HOTEL"}
LIMITS = {"about": 139, "description": 512, "address": 256, "email": 128}


def _cut(text: Optional[str], n: int) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


async def proposed(tenant_id: str) -> Dict[str, Any]:
    """The profile Vula would set, from the brand kit and what it knows about the business."""
    from vula.api import tenants
    from vula.commerce import service, web_basket
    inv = await service.get_invoice_settings(tenant_id) or {}
    cfg = tenants.get_config(tenant_id) or {}
    name = inv.get("trading_as") or inv.get("company_name") or tenants.display_name(tenant_id)
    shop = web_basket.shop_url(tenant_id) if web_basket.shop_enabled(tenant_id) else ""
    about = inv.get("tagline") or (f"Order on WhatsApp — tap Shop to browse {name}." if shop
                                   else f"{name} on WhatsApp — ask us anything.")
    websites = []
    for w in (tenants.store_url(tenant_id), shop):
        w = (w or "").strip()
        if w.startswith(("http://", "https://")) and w not in websites:
            websites.append(w)
    return {
        "about": _cut(about, LIMITS["about"]),
        "description": _cut(cfg.get("description") or inv.get("tagline") or "", LIMITS["description"]),
        "email": _cut(inv.get("company_email"), LIMITS["email"]),
        "address": _cut(inv.get("registered_address"), LIMITS["address"]),
        "websites": websites[:2],
        "vertical": VERTICALS.get(cfg.get("business_type") or "", "OTHER"),
        "picture_url": inv.get("icon_url") or inv.get("logo_url") or None,
    }


async def current(tenant_id: str) -> Dict[str, Any]:
    """What Meta shows now, or {"error"}."""
    from vula.api.whatsapp import _resolve_wa
    creds = await _resolve_wa(tenant_id)
    if not creds:
        return {"error": "Connect this business's WhatsApp number first (Settings › WhatsApp)."}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(f"{GRAPH}/{creds['phone_id']}/whatsapp_business_profile",
                                 params={"fields": FIELDS},
                                 headers={"Authorization": f"Bearer {creds['token']}"})
        if not r.is_success:
            return {"error": _meta_error(r)}
        rows = (r.json() or {}).get("data") or [{}]
        return rows[0]
    except Exception as exc:
        log.warning("WA profile read failed for %s: %s", tenant_id, exc)
        return {"error": "Couldn't reach WhatsApp — try again in a minute."}


def _meta_error(r: httpx.Response) -> str:
    try:
        return ((r.json() or {}).get("error") or {}).get("message") or r.text[:200]
    except Exception:
        return r.text[:200]


async def apply(tenant_id: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    """Set the profile on the shop's WhatsApp number, then read it back. `profile` is what the
    owner approved (the proposal, possibly edited). Blank fields are left as they are."""
    from vula.api.whatsapp import _resolve_wa
    from vula.commerce.wa_templates import _upload_media_handle
    creds = await _resolve_wa(tenant_id)
    if not creds:
        return {"error": "Connect this business's WhatsApp number first (Settings › WhatsApp)."}
    body: Dict[str, Any] = {"messaging_product": "whatsapp"}
    for key, n in LIMITS.items():
        if (profile.get(key) or "").strip():
            body[key] = _cut(profile[key], n)
    sites = [w for w in (profile.get("websites") or []) if str(w).startswith(("http://", "https://"))][:2]
    if sites:
        body["websites"] = sites
    if profile.get("vertical") in set(VERTICALS.values()) | {"OTHER"}:
        body["vertical"] = profile["vertical"]
    picture_note = None
    if profile.get("picture_url"):
        handle, err = await _upload_media_handle(profile["picture_url"], creds["token"])
        if handle:
            body["profile_picture_handle"] = handle
        else:
            picture_note = f"The picture wasn't changed ({err})."
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(f"{GRAPH}/{creds['phone_id']}/whatsapp_business_profile", json=body,
                                  headers={"Authorization": f"Bearer {creds['token']}"})
        if not r.is_success:
            return {"error": f"WhatsApp refused the profile: {_meta_error(r)}"}
    except Exception as exc:
        log.warning("WA profile update failed for %s: %s", tenant_id, exc)
        return {"error": "Couldn't reach WhatsApp — nothing was changed."}
    now = await current(tenant_id)
    return {"applied": True, "profile": now, "picture_note": picture_note}
