"""
vula/api/qs.py — per-tenant Quantity Surveying rate library.

    GET    /v1/qs/rates/{tenant}?q=     list/search rates
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


def search_rates(tenant_id: str, query: str = "", limit: int = 25) -> list[dict]:
    """Search the tenant's rate library by description/code (used by the API + skill)."""
    try:
        q = (_client().table("vula_qs_rates")
             .select("code,description,unit,rate,category,source")
             .eq("tenant_id", tenant_id))
        if query:
            q = q.ilike("description", f"%{query}%")
        return q.limit(limit).execute().data or []
    except Exception as exc:
        log.debug("rate search failed (run migration 017?): %s", exc)
        return []


@router.get("/rates/{tenant_id}")
async def list_rates(tenant_id: str, q: Optional[str] = None) -> dict:
    rows = search_rates(tenant_id, q or "", limit=500)
    return {"tenant_id": tenant_id, "rates": rows, "count": len(rows)}


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
    row = {"tenant_id": tenant_id, **body.model_dump(exclude_none=True)}
    try:
        if body.id:
            rid = row.pop("id")
            row["updated_at"] = "now()"
            _client().table("vula_qs_rates").update(row).eq("id", rid).execute()
            return {"id": rid, **row}
        res = _client().table("vula_qs_rates").insert(row).execute()
        return res.data[0] if res.data else {"error": "insert returned no row"}
    except Exception as exc:
        return {"error": str(exc)}


@router.delete("/rates/{tenant_id}/{rate_id}")
async def delete_rate(tenant_id: str, rate_id: str) -> dict:
    try:
        _client().table("vula_qs_rates").delete().eq("id", rate_id).execute()
        return {"id": rate_id, "deleted": True}
    except Exception as exc:
        return {"error": str(exc)}
