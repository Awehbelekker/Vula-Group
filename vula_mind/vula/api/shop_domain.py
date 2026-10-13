"""
vula/api/shop_domain.py — "Your shop's own address" in Settings (vula/commerce/shop_domain.py).

Under /v1/commerce/{tenant}/admin/…, so the tenant guard already applies; changing it is for
the owner or a manager:
    GET    /v1/commerce/{tenant}/admin/shop-domain    address, DNS records to add, state
    POST   /v1/commerce/{tenant}/admin/shop-domain    {domain} — add it (Railway + the tenant)
    DELETE /v1/commerce/{tenant}/admin/shop-domain    go back to Vula's address
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(tags=["shop-domain"])        # mounted at /v1/commerce


async def _owner_or_manager(request: Request, tenant_id: str) -> None:
    from vula.api.commerce import _may_apply_stock
    if not await _may_apply_stock(request, tenant_id):
        raise HTTPException(status_code=403, detail="Only the owner or a manager can change the shop's address.")


def _with_instructions(st: dict) -> dict:
    from vula.commerce import shop_domain
    if st.get("domain") and st.get("records"):
        st["instructions"] = shop_domain.instructions(st["domain"], st["records"])
    return st


@router.get("/{tenant_id}/admin/shop-domain")
async def shop_domain_status(tenant_id: str):
    from vula.commerce import shop_domain
    return _with_instructions(await shop_domain.status(tenant_id))


@router.post("/{tenant_id}/admin/shop-domain")
async def shop_domain_connect(tenant_id: str, body: dict, request: Request):
    from vula.commerce import shop_domain
    await _owner_or_manager(request, tenant_id)
    try:
        return _with_instructions(await shop_domain.connect(tenant_id, (body or {}).get("domain") or ""))
    except shop_domain.DomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{tenant_id}/admin/shop-domain")
async def shop_domain_disconnect(tenant_id: str, request: Request):
    from vula.commerce import shop_domain
    await _owner_or_manager(request, tenant_id)
    try:
        return await shop_domain.disconnect(tenant_id)
    except shop_domain.DomainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
