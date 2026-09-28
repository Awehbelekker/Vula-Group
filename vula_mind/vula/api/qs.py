"""
vula/api/qs.py — per-tenant Quantity Surveying rate library.

    GET    /v1/qs/rates/{tenant}?q=     list/search rates (own + learned from documents)
    POST   /v1/qs/rates/{tenant}        add or update a rate
    DELETE /v1/qs/rates/{tenant}/{id}   remove a rate

These are the tenant's OWN rates — the calculations skill computes costs from them
(via lookup_rate), never from market figures it would otherwise guess.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(tags=["qs"])


def _client():
    from vula.commerce import service as commerce_service
    return commerce_service._client()


def _manual_rates(tenant_id: str, query: str = "") -> list[dict]:
    rows = (_client().table("vula_qs_rates")
            .select("id,code,description,unit,rate,category,source,notes,updated_at")
            .eq("tenant_id", tenant_id).order("description").limit(2000).execute().data or [])
    words = [w for w in _norm(query).split() if len(w) >= 2]
    if words:
        # every word of the question in the description or code — "brick wall" finds
        # "Breeze block walling, 140mm" by "wall", not only an exact substring
        rows = [r for r in rows
                if all(w in _norm(f"{r.get('description') or ''} {r.get('code') or ''}")
                       or w.rstrip("s") in _norm(r.get("description") or "") for w in words)]
    return rows


def _norm(text: str) -> str:
    from vula.commerce.price_book import norm_key
    return norm_key(text)


def search_rates(tenant_id: str, query: str = "", limit: int = 25,
                 include_learned: bool = True) -> list[dict]:
    """The tenant's rates for the API, the calculations skill and fee proposals: its OWN rates
    first (always authoritative), then — include_learned — rates learned from its own
    invoices, quotes, BOQs and labour payments (vula/commerce/price_book.py), each labelled
    with where it came from. 2026-09-28: DIGG had ~1,000 priced lines on file and 2 rates here."""
    try:
        manual = _manual_rates(tenant_id, query)
    except Exception as exc:
        log.debug("rate search failed (run migration 017?): %s", exc)
        manual = []
    if not include_learned or len(manual) >= limit:
        return manual[:limit]
    try:
        from vula.commerce.price_book import rates as learned_rates
        mine = {_norm(r.get("description") or "") for r in manual}
        learned = [r for r in learned_rates(tenant_id, query, limit=limit)
                   if _norm(r.get("description") or "") not in mine]
    except Exception as exc:
        log.debug("learned rates skipped: %s", exc)
        learned = []
    return (manual + learned)[:limit]


@router.get("/rates/{tenant_id}")
async def list_rates(tenant_id: str, q: Optional[str] = None, kind: Optional[str] = None,
                     project: Optional[str] = None) -> dict:
    from vula.commerce import price_book
    try:
        manual = _manual_rates(tenant_id, q or "")
    except Exception as exc:
        log.debug("rate list failed (run migration 017?): %s", exc)
        manual = []
    learned = price_book.rates(tenant_id, q or "", kind=kind, project=project, limit=1000)
    by_key = {}
    for r in learned:
        by_key.setdefault(_norm(r.get("description") or ""), r)
    mine = set()
    for r in manual:
        key = _norm(r.get("description") or "")
        mine.add(key)
        hit = by_key.get(key)
        if hit:
            d = price_book.drift(r, hit)
            if d:
                r["drift"] = d
    learned = [r for r in learned if _norm(r.get("description") or "") not in mine]
    # `rates` keeps its meaning for Quick Cost / QS Pro: everything usable, own rates first.
    return {"tenant_id": tenant_id, "rates": manual + learned, "own": manual, "learned": learned,
            "count": len(manual) + len(learned)}


class RateIn(BaseModel):
    description: str
    unit: str = "item"
    rate: float
    code: Optional[str] = None
    category: Optional[str] = None
    source: Optional[str] = None
    notes: Optional[str] = None
    id: Optional[str] = None


@router.post("/rates/{tenant_id}")
async def upsert_rate(tenant_id: str, body: RateIn) -> dict:
    from datetime import datetime, timezone
    row = {"tenant_id": tenant_id, **body.model_dump(exclude_none=True)}
    rid = row.pop("id", None)
    try:
        db = _client()
        if not rid:
            # the same description + unit again is an update, not a second row
            same = [r for r in _manual_rates(tenant_id)
                    if _norm(r.get("description") or "") == _norm(body.description)
                    and (r.get("unit") or "") == body.unit]
            rid = same[0]["id"] if same else None
        if rid:
            row["updated_at"] = datetime.now(timezone.utc).isoformat()
            res = (db.table("vula_qs_rates").update(row)
                   .eq("tenant_id", tenant_id).eq("id", rid).execute())
            if not res.data:
                return {"error": "Rate not found."}
            return res.data[0]
        res = db.table("vula_qs_rates").insert(row).execute()
        return res.data[0] if res.data else {"error": "insert returned no row"}
    except Exception as exc:
        return {"error": str(exc)}


@router.delete("/rates/{tenant_id}/{rate_id}")
async def delete_rate(tenant_id: str, rate_id: str) -> dict:
    try:
        (_client().table("vula_qs_rates").delete()
         .eq("tenant_id", tenant_id).eq("id", rate_id).execute())
        return {"id": rate_id, "deleted": True}
    except Exception as exc:
        return {"error": str(exc)}
