"""
vula/commerce/geo.py — delivery geography (migration 098).

Facebook-Marketplace-style coverage: the tenant's shop pin (origin_lat/lng) + a radius in km.
- `coverage(tenant_id, lat, lng)` — deterministic in/out verdict for a coordinate (used the
  moment a customer shares a WhatsApp location pin).
- `geocode(query)` — free OSM/Nominatim lookup with a DB cache (their fair-use policy is
  ~1 req/s with a proper User-Agent; the cache means each unique query hits them once ever).
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any, Optional

import httpx

log = logging.getLogger(__name__)

_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_UA = "VulaCommerce/1.0 (delivery coverage; contact: awehbelekker@gmail.com)"


def _client():
    from vula.commerce import service as cs
    return cs._client()


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


async def geocode(query: str) -> Optional[dict]:
    """query → {lat, lng, label} via cache-then-Nominatim. SA-biased. None if not found."""
    q = " ".join((query or "").lower().split())
    if not q:
        return None
    if "south africa" not in q and "za" != q[-2:]:
        q_search = f"{q}, South Africa"
    else:
        q_search = q
    try:
        hit = (_client().table("commerce_geo_cache").select("*")
               .eq("query", q).limit(1).execute().data or [])
        if hit:
            h = hit[0]
            if h.get("lat") is None:
                return None  # cached miss
            return {"lat": h["lat"], "lng": h["lng"], "label": h.get("label") or query}
    except Exception as exc:
        log.debug("geo cache read skipped (run migration 098?): %s", exc)

    result = None
    try:
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": _UA}) as client:
            resp = await client.get(_NOMINATIM, params={
                "q": q_search, "format": "json", "limit": 1, "countrycodes": "za"})
            rows = resp.json() if resp.status_code == 200 else []
        if rows:
            result = {"lat": float(rows[0]["lat"]), "lng": float(rows[0]["lon"]),
                      "label": (rows[0].get("display_name") or query).split(",")[0]}
    except Exception as exc:
        log.warning("geocode failed for %r: %s", query, exc)
        return None  # transient failure — don't cache

    try:  # cache hit AND miss (miss = lat NULL) so we never re-ask Nominatim the same thing
        _client().table("commerce_geo_cache").upsert({
            "query": q, "lat": (result or {}).get("lat"), "lng": (result or {}).get("lng"),
            "label": (result or {}).get("label")}, on_conflict="query").execute()
    except Exception:
        pass
    return result


def get_origin(tenant_id: str) -> Optional[dict]:
    """The tenant's shop pin + radius, or None if not configured."""
    try:
        from vula.commerce.order_workflow import get_order_settings
        cfg = get_order_settings(tenant_id)
        if cfg.get("origin_lat") is not None and cfg.get("delivery_radius_km"):
            return {"lat": cfg["origin_lat"], "lng": cfg["origin_lng"],
                    "label": cfg.get("origin_label") or "the shop",
                    "radius_km": float(cfg["delivery_radius_km"])}
    except Exception as exc:
        log.debug("origin read failed: %s", exc)
    return None


def coverage(tenant_id: str, lat: float, lng: float) -> Optional[dict]:
    """Deterministic delivery verdict for a coordinate. None if no origin/radius configured."""
    o = get_origin(tenant_id)
    if not o:
        return None
    dist = haversine_km(o["lat"], o["lng"], lat, lng)
    return {"covered": dist <= o["radius_km"], "distance_km": round(dist, 1),
            "radius_km": o["radius_km"], "origin_label": o["label"]}


# ── named-area coverage ─────────────────────────────────────────────────────────────────────
# coverage() above needs an origin pin + radius. Most commerce tenants configure a plain LIST of
# suburb names instead (migration 070) and never set a pin — off-the-hook has 13 named areas and
# no origin at all. For those, nothing decided anything: the storefront prompt told the model to
# escalate EVERY out-of-area delivery question via ask_team, an over-correction from the
# 2026-07-16 "Ja, ons lewer na Timbuktu" hallucination. Measured on real data, that turned each
# such question into a handoff nobody answered — "Do you deliver to Timbuktu" (2026-07-17) and
# "Do you deliver to Bloemfontein?" (2026-09-02) both escalated, both expired unanswered, both
# left the customer with 48h of silence and then an apology. A fish shop in Table View can
# answer Bloemfontein itself.
#
# The tenant's own setting says "we ONLY deliver to these areas", so a name that matches nothing
# is a NO by their own configuration. The only real hazard is a name VARIANT of a covered area
# ("Blouberg" for "Bloubergstrand", "Tableview" for "Table View"), which is what containment
# matching and the shared-word "unsure" band are for. Unsure still escalates — the gap this
# closes is confident answers being escalated, not judgement being replaced.

def _norm_area(s: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _words(s: str) -> set:
    # 3 letters, not 4: a bare "Bay" shares nothing 4+ with "Big Bay" and would otherwise be a
    # confident NO for an area that IS covered. The unsure band exists to catch exactly that, so
    # it should be the generous one — a needless escalation costs a question, a wrong "no
    # we don't deliver there" costs the sale.
    return {w for w in s.split() if len(w) >= 3}


def area_verdict(tenant_id: str, place: str) -> Optional[dict]:
    """Is `place` inside this tenant's configured delivery areas?

    Returns {"verdict": "covered"|"not_covered"|"unsure", "areas": [...], "matched": str|None}
    or None when the tenant has no named areas configured (caller must keep escalating).
    """
    try:
        from vula.commerce.order_workflow import get_order_settings
        areas = (get_order_settings(tenant_id) or {}).get("delivery_areas") or []
    except Exception as exc:
        log.debug("delivery-area read failed for %s: %s", tenant_id, exc)
        return None
    areas = [str(a) for a in areas if str(a).strip()]
    if not areas:
        return None

    p = _norm_area(place)
    if not p:
        return None
    squashed = p.replace(" ", "")
    for a in areas:
        n = _norm_area(a)
        # Exact, or the same name written without the space ("Tableview" / "Table View").
        if p == n or squashed == n.replace(" ", ""):
            return {"verdict": "covered", "areas": areas, "matched": a}
        # The place is MORE specific than a configured area ("Milnerton Ridge", "Table View
        # North") — the area name appears in it as whole words.
        if re.search(rf"\b{re.escape(n)}\b", p):
            return {"verdict": "covered", "areas": areas, "matched": a}
        # The place is a shortened form of a configured area — only as a PREFIX
        # ("Blouberg" → "Bloubergstrand", "Melkbos" → "Melkbosstrand"). Plain substring would
        # match a bare generic word against its qualifier ("Beach" inside "West Beach"), which
        # is not a name variant at all; that falls through to the unsure band below.
        if len(p) >= 4 and n.startswith(p):
            return {"verdict": "covered", "areas": areas, "matched": a}

    pw = _words(p)
    for a in areas:
        if pw & _words(_norm_area(a)):
            return {"verdict": "unsure", "areas": areas, "matched": a}
    return {"verdict": "not_covered", "areas": areas, "matched": None}
