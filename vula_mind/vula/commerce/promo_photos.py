"""
vula/commerce/promo_photos.py — the business's own post pictures as its website product photos.

2026-10-24 (Off the Hook): 44 of OTH's 45 website pictures were AI-made from the product name,
and some don't look like OTH's real fish. Staci's posts have real photos of the real product.
A forwarded post's picture is matched to the product it shows, checked by a vision model (a real
photo of the raw product as sold — not a poster covered in text, a cooked dish or people), and
offered to the team. It only becomes the website picture after "Use photo" (set_product_photo,
read back). Nothing here changes a product by itself.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Words that say how a product is cut or packed: they break a tie between two products of the
# same fish ("Kingklip Fillets" vs "Kingklip Centre Cuts"), but never make a match on their own.
_CUT = {"fillet", "steak", "cut", "centre", "center", "portion", "loin", "piece", "slice", "tube",
        "strip", "stick", "cake", "whole", "breast", "thigh", "drumstick", "wing"}
# Words that say nothing about which product it is.
_NOISE = {"fresh", "frozen", "pack", "per", "kg", "1kg", "box", "skinless", "boneless", "clean",
          "the", "and", "with", "of", "for", "our", "this", "week", "order", "now", "today",
          "available", "stock", "limited", "new", "special", "sale", "all", "off", "must", "go"}
_FISH_FIRST = ("fresh_fish", "frozen_seafood")


def needs_photo(product: Dict[str, Any]) -> bool:
    """No picture yet, or an AI-made one (stored as …-ai-….jpg by the AI photo endpoint)."""
    url = product.get("image_url") or ""
    return not url or "-ai-" in url


def _tokens(text: str) -> set:
    from core.skills.commerce_assistant import _name_tokens
    return {t for t in _name_tokens(text) if t not in _NOISE and not t.isdigit()}


def _score(product: Dict[str, Any], caption: str, shows: str) -> int:
    """Species words count 3, cut words 1; what the photo shows counts double. 0 unless the
    post names the product's species (a "fresh fillets" sale names no product)."""
    name = _tokens(product.get("name") or "")
    species = name - _CUT
    score = 0
    for text, weight in ((caption, 1), (shows, 2)):
        seen = _tokens(text)
        score += weight * (3 * len(species & seen) + len(name & _CUT & seen))
    hit = species & (_tokens(caption) | _tokens(shows))
    return score if hit else 0


def pick(products: List[Dict[str, Any]], caption: str, shows: str = "") -> Optional[Dict[str, Any]]:
    """The one product a post is about, or None when nothing (or more than one) fits."""
    scored = sorted(((_score(p, caption, shows), p) for p in products), key=lambda x: -x[0])
    if not scored or scored[0][0] <= 0:
        return None
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        return None
    return scored[0][1]


async def look(image_bytes: bytes) -> Optional[Dict[str, Any]]:
    """What the photo shows, and whether it's a real photo of the raw product as sold.
    None when it can't be checked — then nothing is offered (fail closed)."""
    import litellm
    from core.llm_router import resolve_cloud_vision_route
    route = resolve_cloud_vision_route()
    if not route or not image_bytes:
        return None
    model, api_key, api_base = route
    litellm.drop_params = True
    b64 = base64.b64encode(image_bytes).decode()
    try:
        resp = await litellm.acompletion(
            model=model, temperature=0.0, max_tokens=120, api_key=api_key, api_base=api_base,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": (
                    "This picture is from a food shop's own WhatsApp post. Reply with JSON only: "
                    '{"raw_product_photo": true if it is a real photograph of the raw, uncooked '
                    "product as it is sold (not a cooked or plated dish, not people, not a drawing), "
                    '"mostly_text": true if text or a poster layout covers much of it, '
                    '"shows": a few words naming the product, e.g. "raw skinless kingklip fillets"}')},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}])
        raw = resp.choices[0].message.content or ""
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        return json.loads(m.group(0)) if m else None
    except Exception as exc:  # noqa: BLE001
        logger.info("promo photo check skipped: %s", type(exc).__name__)
        return None


async def match(tenant_id: str, caption: str, image_bytes: bytes) -> Optional[Dict[str, Any]]:
    """{"product": row, "shows": str} when the picture is a usable photo of a product whose
    website picture is AI-made or missing; else None."""
    from vula.commerce import service
    products = [p for p in await service.list_products(tenant_id, in_stock_only=False,
                                                       statuses={"active"}) if needs_photo(p)]
    if not products:
        return None
    seen = await look(image_bytes)
    if not seen or not seen.get("raw_product_photo") or seen.get("mostly_text"):
        return None
    product = pick(products, caption or "", str(seen.get("shows") or ""))
    return {"product": product, "shows": seen.get("shows") or ""} if product else None


async def still_ai(tenant_id: str, limit: int = 12) -> List[str]:
    """Product names still without a real photo — fish and seafood first."""
    from vula.commerce import service
    rows = [p for p in await service.list_products(tenant_id, in_stock_only=False,
                                                   statuses={"active"}) if needs_photo(p)]
    rows.sort(key=lambda p: (0 if (p.get("category") or "") in _FISH_FIRST else 1, p.get("name") or ""))
    return [p.get("name") or "" for p in rows[:limit]]


async def set_photo(tenant_id: str, product_id: str, image_url: str) -> Dict[str, Any]:
    """Make a real photo the product's website picture. AI pictures leave the gallery (they stay
    in storage). Returns the product as read back."""
    from vula.commerce import service
    db = service._client()
    row = (db.table("commerce_products").select("id,name,image_url,images")
           .eq("tenant_id", tenant_id).eq("id", product_id).limit(1).execute().data or [None])[0]
    if not row:
        return {"error": "That product wasn't found."}
    keep = [u for u in (row.get("images") or []) if u and u != image_url and "-ai-" not in u]
    await service.update_product(tenant_id, product_id,
                                 {"image_url": image_url, "images": [image_url] + keep[:4]})
    back = (db.table("commerce_products").select("id,name,image_url")
            .eq("tenant_id", tenant_id).eq("id", product_id).limit(1).execute().data or [{}])[0]
    return back
