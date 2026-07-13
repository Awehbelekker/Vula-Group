"""
vula/api/subscriptions.py — customer product subscriptions (recurring orders) REST API.

    GET    /v1/subscriptions/{tenant}                 list (?status=&phone=)
    POST   /v1/subscriptions/{tenant}                 create a subscription
    PATCH  /v1/subscriptions/{tenant}/{id}            edit items/cadence/next_run
    POST   /v1/subscriptions/{tenant}/{id}/status     pause / resume / cancel
    POST   /v1/subscriptions/{tenant}/jobs/run        create due orders now (scheduler/backstop)
"""
from __future__ import annotations

import logging
from typing import Any, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from vula.commerce import subscriptions as subs

log = logging.getLogger(__name__)
router = APIRouter(tags=["subscriptions"])


class SubItem(BaseModel):
    product_id: Optional[str] = None
    product_name: str
    quantity: float = 1
    unit_price_cents: int = 0


class SubscriptionIn(BaseModel):
    customer_name: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_email: Optional[str] = None
    delivery_address: Optional[str] = None
    delivery_slot: Optional[str] = None
    items: List[SubItem]
    delivery_cents: int = 0
    payment_method: Optional[str] = None
    cadence: str = "weekly"
    next_run: Optional[str] = None      # YYYY-MM-DD; defaults to one cadence from today
    channel: str = "dashboard"
    note: Optional[str] = None


@router.get("/{tenant_id}")
async def list_subscriptions(tenant_id: str, status: Optional[str] = None,
                             phone: Optional[str] = None) -> dict:
    return {"subscriptions": await subs.list_subs(tenant_id, status=status, phone=phone)}


@router.post("/{tenant_id}")
async def create_subscription(tenant_id: str, body: SubscriptionIn) -> dict:
    try:
        return await subs.create(tenant_id, body.model_dump())
    except Exception as exc:
        return {"error": f"{exc} (run migration 051?)"}


class PatchIn(BaseModel):
    items: Optional[List[SubItem]] = None
    cadence: Optional[str] = None
    next_run: Optional[str] = None
    delivery_cents: Optional[int] = None
    payment_method: Optional[str] = None
    delivery_address: Optional[str] = None
    delivery_slot: Optional[str] = None
    customer_name: Optional[str] = None
    note: Optional[str] = None


@router.patch("/{tenant_id}/{sub_id}")
async def update_subscription(tenant_id: str, sub_id: str, body: PatchIn) -> dict:
    patch: dict[str, Any] = {k: v for k, v in body.model_dump().items() if v is not None}
    return {"subscription": await subs.update(tenant_id, sub_id, patch)}


class StatusIn(BaseModel):
    status: str


@router.post("/{tenant_id}/{sub_id}/status")
async def set_status(tenant_id: str, sub_id: str, body: StatusIn) -> dict:
    try:
        return {"subscription": await subs.set_status(tenant_id, sub_id, body.status)}
    except ValueError as exc:
        return {"error": str(exc)}


@router.post("/{tenant_id}/jobs/run")
async def run_due(tenant_id: str) -> dict:
    return {"created": await subs.process_due(tenant_id)}
