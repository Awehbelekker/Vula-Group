"""vula/api/buying_api.py — Settings › Buying (vula/commerce/buying.py, migration 246).

Under /v1/commerce/{tenant}/admin/…, so the tenant guard already applies; changing the settings or
the shop list is for the owner or a manager:
    GET  /v1/commerce/{tenant}/admin/buying                    settings, shops, purchases, this month
    PUT  /v1/commerce/{tenant}/admin/buying                    settings (rands in, cents stored)
    POST /v1/commerce/{tenant}/admin/buying/shops              {name, domain?, how, supplier_id?}
    POST /v1/commerce/{tenant}/admin/buying/shops/{id}/remove  take a shop off the list (kept on record)
Nothing here buys anything; purchases are asked for on WhatsApp and approved there.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

log = logging.getLogger(__name__)
router = APIRouter(tags=["buying"])        # mounted at /v1/commerce


async def _owner_or_manager(request: Request, tenant_id: str) -> None:
    from vula.api.commerce import _may_apply_stock
    if not await _may_apply_stock(request, tenant_id):
        raise HTTPException(status_code=403, detail="Only the owner or a manager can change buying.")


def _view(tenant_id: str) -> dict:
    from vula.commerce import buying
    cfg = buying.settings_for(tenant_id)
    return {"settings": cfg, "shops": buying.shops(tenant_id), "purchases": buying.recent(tenant_id),
            "month_spent_cents": buying.month_spent(tenant_id),
            "approvers": [{"name": a.get("name") or "", "phone": a["phone"]} for a in buying.approvers(tenant_id, cfg)]}


@router.get("/{tenant_id}/admin/buying")
async def buying_view(tenant_id: str) -> dict:
    try:
        return await asyncio.to_thread(_view, tenant_id)
    except Exception as exc:  # noqa: BLE001 — e.g. migration 246 not applied yet
        log.warning("buying view for %s failed: %s", tenant_id, exc)
        raise HTTPException(status_code=503, detail="Buying isn't available just now — please try again.")


class BuyingSettingsIn(BaseModel):
    enabled: Optional[bool] = None
    per_purchase_limit: Optional[float] = None      # rands
    monthly_limit: Optional[float] = None           # rands
    who_can_ask: Optional[str] = None
    approver_phones: Optional[List[str]] = None
    default_project: Optional[str] = None
    default_account: Optional[str] = None
    by: Optional[str] = None


@router.put("/{tenant_id}/admin/buying")
async def buying_save(tenant_id: str, body: BuyingSettingsIn, request: Request) -> dict:
    from vula.commerce import buying
    await _owner_or_manager(request, tenant_id)
    patch = body.model_dump(exclude_unset=True)
    by = patch.pop("by", None)
    try:
        await asyncio.to_thread(buying.save_settings, tenant_id, patch, by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return await asyncio.to_thread(_view, tenant_id)


class ShopIn(BaseModel):
    name: str
    domain: Optional[str] = None
    how: str = "checkout_link"
    supplier_id: Optional[str] = None
    by: Optional[str] = None


@router.post("/{tenant_id}/admin/buying/shops")
async def buying_add_shop(tenant_id: str, body: ShopIn, request: Request) -> dict:
    from vula.commerce import buying
    await _owner_or_manager(request, tenant_id)
    try:
        await asyncio.to_thread(buying.add_shop, tenant_id, body.name, domain=body.domain, how=body.how,
                                supplier_id=body.supplier_id, by=body.by)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return await asyncio.to_thread(_view, tenant_id)


@router.post("/{tenant_id}/admin/buying/shops/{shop_id}/remove")
async def buying_remove_shop(tenant_id: str, shop_id: str, request: Request) -> dict:
    from vula.commerce import buying
    await _owner_or_manager(request, tenant_id)
    try:
        await asyncio.to_thread(buying.remove_shop, tenant_id, shop_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return await asyncio.to_thread(_view, tenant_id)
