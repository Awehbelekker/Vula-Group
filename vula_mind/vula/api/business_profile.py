"""Business profile — dashboard endpoints (vula/commerce/business_profile.py).

Mounted under /v1/commerce, so /v1/commerce/{tenant_id}/admin/... is covered by the tenant guard
(server._TENANT_GUARD_RES): only a member of that tenant (or master / the API key) gets here."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from vula.commerce import business_profile as bp

router = APIRouter(tags=["business-profile"])


@router.get("/{tenant_id}/admin/business-profile")
async def get_profile(tenant_id: str) -> dict:
    return bp.status(tenant_id)


@router.put("/{tenant_id}/admin/business-profile")
async def put_profile(tenant_id: str, body: dict) -> dict:
    answers = (body or {}).get("answers")
    if not isinstance(answers, dict):
        raise HTTPException(status_code=400, detail="answers must be an object")
    res = await bp.save_answers(tenant_id, answers, by="dashboard")
    if not res.get("saved"):
        raise HTTPException(status_code=500, detail=res.get("error") or "Couldn't save")
    return res
