"""
vula/api/till.py — the dashboard's Till screen (vula/commerce/till.py).

Under /v1/commerce/{tenant}/admin/…, so the tenant guard already applies (an accountant's login
reaches none of it). Anyone on the team who works the counter can ring up a sale.

    GET  …/admin/till/products          what the till shows: prices charged now, stock, barcodes
    POST …/admin/till/sales             {items, method, sale_id, tendered_cents?, customer_*?, discount_code?}
    GET  …/admin/till/summary?date=     the day's takings by payment method and by who rang them up
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from vula.commerce import till

router = APIRouter(tags=["till"])               # mounted at /v1/commerce


def _refuse(exc: till.TillError) -> HTTPException:
    detail = {"message": str(exc), **exc.detail} if exc.detail else str(exc)
    return HTTPException(status_code=exc.status, detail=detail)


@router.get("/{tenant_id}/admin/till/products")
async def till_products(tenant_id: str) -> dict:
    return {"products": await till.products(tenant_id),
            "methods": [{"id": k, "label": v} for k, v in till.METHODS.items()]}


@router.post("/{tenant_id}/admin/till/sales")
async def till_sale(tenant_id: str, body: dict, request: Request) -> dict:
    b = body or {}
    from vula import acting
    who = await acting.from_dashboard(request.headers.get("authorization", ""), tenant_id)
    try:
        with acting.acting_as(who):
            return {"sale": await till.sale(
                tenant_id, items=b.get("items"), method=b.get("method"), sale_id=str(b.get("sale_id") or ""),
                tendered_cents=b.get("tendered_cents"), customer_name=str(b.get("customer_name") or ""),
                customer_phone=str(b.get("customer_phone") or ""), discount_code=str(b.get("discount_code") or ""))}
    except till.TillError as exc:
        raise _refuse(exc) from exc


@router.get("/{tenant_id}/admin/till/summary")
async def till_summary(tenant_id: str, date: Optional[str] = None) -> dict:
    try:
        return till.summary(tenant_id, date)
    except till.TillError as exc:
        raise _refuse(exc) from exc
