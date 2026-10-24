"""
vula/commerce/promotions.py — promotions the business forwards into its own WhatsApp line.

2026-10-23 (Off the Hook): Ian forwarded about 20 of OTH's old marketing posts ("FRESH TUNA –
LIMITED STOCK", "Last chance to order fresh chicken before 12pm"…) into OTH's Vula line. Each one
was answered as a customer: tap menus, "we'll be in touch", and one put 2 × Hake Fillets in his
cart. A forward from the team is now kept here (migration 220) and used two ways:

- examples(): the owner asks Vula to write a promotion → it matches the business's own tone.
- for_customers(): "what's on special?" → the last 7 days' promotions, each dated, with every
  rand amount masked. Prices still come only from the catalogue (list_products), under the
  customer price check in commerce_assistant — a promo's own figure may be out of date.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_BODY = 1500
DEDUPE_DAYS = 30
CUSTOMER_DAYS = 7

_RAND_RE = re.compile(r"\bR\s?\d[\d,]*(?:\s\d{3})*(?:\.\d{1,2})?(?:\s*/\s*(?:kg|each|ea|pack|pk|unit))?", re.I)


def _client():
    from vula.commerce import service
    return service._client()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip()).lower()


def save(tenant_id: str, body: str, media_id: Optional[str] = None, mime: Optional[str] = None,
         by: Optional[str] = None, image_url: Optional[str] = None) -> Optional[dict]:
    """Keep one forwarded promotion. The same text forwarded again within 30 days is not stored
    twice (returns the existing row with "duplicate": True). A picture with no words is kept
    when its picture was stored (image_url, migration 221). Returns None on failure."""
    body = (body or "").strip()[:MAX_BODY]
    if not tenant_id or not (body or image_url):
        return None
    db = _client()
    if not body:
        try:
            out = db.table("vula_promotions").insert({
                "tenant_id": tenant_id, "body": "", "media_id": media_id, "mime": mime,
                "source": "forwarded", "added_by": by, "image_url": image_url,
            }).execute().data or []
            return out[0] if out else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("promotion picture not saved for %s (migration 221?): %s", tenant_id, exc)
            return None
    since = (datetime.now(timezone.utc) - timedelta(days=DEDUPE_DAYS)).isoformat()
    try:
        rows = (db.table("vula_promotions").select("id,body,created_at")
                .eq("tenant_id", tenant_id).gte("created_at", since)
                .order("created_at", desc=True).limit(200).execute().data or [])
        key = _norm(body)
        for r in rows:
            if _norm(r.get("body") or "") == key:
                return {**r, "duplicate": True}
        row = {"tenant_id": tenant_id, "body": body, "media_id": media_id, "mime": mime,
               "source": "forwarded", "added_by": by}
        if image_url:
            row["image_url"] = image_url
        out = db.table("vula_promotions").insert(row).execute().data or []
        return out[0] if out else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("promotion not saved for %s (migration 220?): %s", tenant_id, exc)
        return None


def recent(tenant_id: str, days: int = CUSTOMER_DAYS, limit: int = 20,
           sources: tuple = ("forwarded", "example")) -> List[Dict]:
    """The newest promotions first, from the last `days` days. source "example" = kept for tone
    only (the team said they're old posts); "removed" = the team said undo."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        return (_client().table("vula_promotions").select("id,body,media_id,source,created_at,added_by")
                .eq("tenant_id", tenant_id).gte("created_at", since).in_("source", list(sources))
                .order("created_at", desc=True).limit(limit).execute().data or [])
    except Exception as exc:  # noqa: BLE001
        logger.debug("promotions read skipped for %s: %s", tenant_id, exc)
        return []


def mask_prices(text: str) -> str:
    """'Swordfish R320/kg' → 'Swordfish (price on request)'. A promo's price may be stale; the
    customer gets the catalogue price from list_products instead."""
    return _RAND_RE.sub("(price on request)", text or "")


def _day(ts: str) -> str:
    try:
        d = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
        return f"{d.day} {d.strftime('%b')}"
    except Exception:
        return ""


def for_customers(tenant_id: str, days: int = CUSTOMER_DAYS, limit: int = 8) -> str:
    """The last week's promotions as dated lines, prices masked. "" when there are none."""
    lines = []
    for r in recent(tenant_id, days=days, limit=limit, sources=("forwarded",)):
        body = re.sub(r"\s+", " ", mask_prices(r.get("body") or "")).strip()
        if not body:
            continue
        if len(body) > 300:
            body = body[:297].rstrip() + "…"
        when = _day(r.get("created_at") or "")
        lines.append(f"- From the {when} promotion: {body}" if when else f"- {body}")
    return "\n".join(lines)


def set_source(tenant_id: str, ids: List[str], source: str) -> int:
    """Mark saved promotions "example" (tone only) or "removed". Returns how many now read back
    with that source — the count the reply reports."""
    if not ids or source not in ("forwarded", "example", "removed"):
        return 0
    db = _client()
    try:
        db.table("vula_promotions").update({"source": source}).eq("tenant_id", tenant_id) \
            .in_("id", ids).execute()
        back = (db.table("vula_promotions").select("id").eq("tenant_id", tenant_id)
                .in_("id", ids).eq("source", source).execute().data or [])
        return len(back)
    except Exception as exc:  # noqa: BLE001
        logger.warning("promotion source not changed for %s: %s", tenant_id, exc)
        return 0


def examples(tenant_id: str, n: int = 5) -> List[str]:
    """The business's own recent promotions, newest first, to match their tone and layout."""
    return [(r.get("body") or "").strip() for r in recent(tenant_id, days=180, limit=n)
            if (r.get("body") or "").strip()]
