"""
vula/api/pipeline.py — the deals pipeline (vula/pipeline/service.py, migration 255).

Mounted under /v1/commerce, so every route here (/{tenant}/admin/...) is behind the tenant guard
(server._TENANT_GUARD_RES). Each route also requires the `pipeline` module, so a business that
hasn't switched it on can't write deals by calling the API directly.

    GET    /v1/commerce/{tenant}/admin/deals                       board: stages, deals, summary, linked listings
    POST   /v1/commerce/{tenant}/admin/deals                       create (no id) / update
    POST   /v1/commerce/{tenant}/admin/deals/{id}/move             {stage, position}
    DELETE /v1/commerce/{tenant}/admin/deals/{id}
    POST   /v1/commerce/{tenant}/admin/deals/from-enquiry/{enquiry_id}
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from vula.pipeline import service as ps

log = logging.getLogger(__name__)
router = APIRouter(tags=["pipeline"])


def _require_module(tenant_id: str) -> None:
    try:
        from vula.api.tenants import enabled_modules
        on = "pipeline" in (enabled_modules(tenant_id) or [])
    except Exception:
        on = False
    if not on:
        raise HTTPException(status_code=404, detail="The pipeline isn't switched on for this business.")


async def _linked_listings(tenant_id: str, deals) -> Dict[str, Dict[str, Any]]:
    ids = {d.get("ref_id") for d in deals if d.get("ref_type") == "property" and d.get("ref_id")}
    if not ids:
        return {}
    from vula.listings import service as ls
    rows = await ls.list_properties(tenant_id, limit=500)
    return {p["id"]: {"title": p.get("title"), "image": (p.get("images") or [None])[0],
                      "price_cents": p.get("price_cents"), "listing_type": p.get("listing_type"),
                      "suburb": p.get("suburb")}
            for p in rows if p["id"] in ids}


@router.get("/{tenant_id}/admin/deals")
async def board(tenant_id: str) -> Dict[str, Any]:
    _require_module(tenant_id)
    deals = await ps.list_deals(tenant_id)
    return {"stages": ps.stages_for(tenant_id), "deals": deals, "summary": ps.summary(tenant_id, deals),
            "listings": await _linked_listings(tenant_id, deals)}


@router.post("/{tenant_id}/admin/deals")
async def save(tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    _require_module(tenant_id)
    try:
        return {"deal": await ps.save_deal(tenant_id, body or {})}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except LookupError:
        raise HTTPException(status_code=404, detail="Deal not found")


class MoveIn(BaseModel):
    stage: str
    position: Optional[int] = None


@router.post("/{tenant_id}/admin/deals/{deal_id}/move")
async def move(tenant_id: str, deal_id: str, body: MoveIn) -> Dict[str, Any]:
    _require_module(tenant_id)
    patch: Dict[str, Any] = {"id": deal_id, "stage": body.stage}
    if body.position is not None:
        patch["position"] = body.position
    try:
        deal = await ps.save_deal(tenant_id, patch)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except LookupError:
        raise HTTPException(status_code=404, detail="Deal not found")
    if deal.get("stage") != body.stage:                      # read back
        raise HTTPException(status_code=409, detail="The move didn't save — please try again.")
    return {"deal": deal}


@router.delete("/{tenant_id}/admin/deals/{deal_id}")
async def delete(tenant_id: str, deal_id: str) -> Dict[str, Any]:
    _require_module(tenant_id)
    await ps.delete_deal(tenant_id, deal_id)
    return {"ok": True}


@router.post("/{tenant_id}/admin/deals/from-enquiry/{enquiry_id}")
async def from_enquiry(tenant_id: str, enquiry_id: str) -> Dict[str, Any]:
    """Turn a website/WhatsApp enquiry into a deal at the first stage, and mark the enquiry
    contacted if it was still new. Asking twice returns the deal already made from it."""
    _require_module(tenant_id)
    from vula.listings import service as ls
    rows = (ls._client().table(ls.ENQUIRIES).select("*").eq("tenant_id", tenant_id)
            .eq("id", enquiry_id).limit(1).execute().data or [])
    if not rows:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    enq = rows[0]
    existing = [d for d in await ps.list_deals(tenant_id) if d.get("enquiry_id") == enquiry_id]
    if existing:
        return {"deal": existing[0], "existing": True}
    prop = await ls.get_property(tenant_id, property_id=enq["property_id"]) if enq.get("property_id") else None
    deal = await ps.save_deal(tenant_id, {
        "title": f"{enq.get('name') or 'Enquiry'} — {prop['title']}" if prop else (enq.get("name") or "Enquiry"),
        "contact_name": enq.get("name"), "contact_phone": enq.get("whatsapp"), "contact_email": enq.get("email"),
        "ref_type": "property" if prop else None, "ref_id": prop["id"] if prop else None,
        "enquiry_id": enquiry_id, "notes": enq.get("message"),
    })
    if (enq.get("status") or "new") == "new":
        try:
            await ls.set_enquiry_status(tenant_id, enquiry_id, "contacted")
        except Exception as exc:
            log.debug("enquiry status not moved: %s", exc)
    return {"deal": deal, "existing": False}
