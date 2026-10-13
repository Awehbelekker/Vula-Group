"""
vula/commerce/shop_stats.py — the WhatsApp shop's funnel for Home: opened → basket → sent to
WhatsApp → ordered → paid, and what customers searched for (and didn't find).

2026-10-13 (the WhatsApp shop plan). Opens, baskets and searches are recorded here as tiny rows
(commerce_shop_events, migration 209 — no phone, no message text); orders and payments come from
commerce_orders. Recording is best-effort and off the request path: a missing table or a slow
database never slows or breaks the shop.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

KINDS = {"shop_open", "basket", "basket_claimed", "search", "search_miss"}
PAID = {"paid", "confirmed", "packing", "dispatched", "delivered", "completed"}
_BOTS = re.compile(r"bot|crawl|spider|facebookexternalhit|whatsapp/|preview|curl|python", re.I)


def clean_term(text: Optional[str]) -> Optional[str]:
    """A search worth showing the owner: short, lower-case, no digits (never a phone number,
    address or order number)."""
    t = re.sub(r"\s+", " ", (text or "").strip().lower()).strip(" ?!.")
    if not t or len(t) > 40 or re.search(r"\d", t):
        return None
    return t


def is_bot(user_agent: Optional[str]) -> bool:
    return bool(_BOTS.search(user_agent or ""))


def _write(tenant_id: str, kind: str, term: Optional[str]) -> None:
    try:
        from vula.commerce import service
        service._client().table("commerce_shop_events").insert(
            {"tenant_id": tenant_id, "kind": kind, "term": term}).execute()
    except Exception as exc:
        log.debug("shop event %s not recorded (run migration 209?): %s", kind, exc)


def record(tenant_id: str, kind: str, term: Optional[str] = None) -> None:
    """Fire and forget. Never raises, never blocks the caller; nothing during a benchmark dry run."""
    if kind not in KINDS or not tenant_id:
        return
    try:
        from core import dry_run
        if dry_run.active():
            return
    except Exception:
        pass
    term = clean_term(term) if term else None
    if kind in ("search", "search_miss") and not term:
        return
    try:
        asyncio.get_running_loop().run_in_executor(None, _write, tenant_id, kind, term)
    except RuntimeError:
        _write(tenant_id, kind, term)


def funnel(tenant_id: str, days: int = 7) -> Dict[str, Any]:
    from vula.commerce import service
    db = service._client()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    counts: Counter = Counter()
    searches: Counter = Counter()
    misses: Counter = Counter()
    tracked = True
    try:
        rows = (db.table("commerce_shop_events").select("kind,term").eq("tenant_id", tenant_id)
                .gte("created_at", since).limit(20000).execute().data or [])
        for r in rows:
            counts[r["kind"]] += 1
            if r.get("term") and r["kind"] == "search":
                searches[r["term"]] += 1
            elif r.get("term") and r["kind"] == "search_miss":
                misses[r["term"]] += 1
    except Exception as exc:
        tracked = False
        log.debug("shop funnel events unavailable for %s: %s", tenant_id, exc)
    try:
        orders = (db.table("commerce_orders").select("status,total_cents,channel").eq("tenant_id", tenant_id)
                  .gte("created_at", since).limit(5000).execute().data or [])
    except Exception:
        orders = []
    orders = [o for o in orders if o.get("channel") in ("whatsapp", "web")]
    paid = [o for o in orders if o.get("status") in PAID]
    return {
        "days": days, "tracked": tracked,
        "steps": [
            {"key": "shop_open", "label": "Opened the shop", "count": counts["shop_open"]},
            {"key": "basket", "label": "Filled a basket", "count": counts["basket"]},
            {"key": "basket_claimed", "label": "Sent it to WhatsApp", "count": counts["basket_claimed"]},
            {"key": "ordered", "label": "Placed an order", "count": len(orders)},
            {"key": "paid", "label": "Paid", "count": len(paid)},
        ],
        "paid_cents": sum(int(o.get("total_cents") or 0) for o in paid),
        "searches": counts["search"] + counts["search_miss"],
        "top_searches": [{"term": t, "count": n} for t, n in searches.most_common(5)],
        "not_found": [{"term": t, "count": n} for t, n in misses.most_common(5)],
    }
