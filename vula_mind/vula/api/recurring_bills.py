"""
vula/api/recurring_bills.py — recurring bills REST API.

    GET    /v1/recurring-bills/{tenant}                 list (?status=)
    POST   /v1/recurring-bills/{tenant}                 create a recurring bill
    PATCH  /v1/recurring-bills/{tenant}/{id}             edit amount/cadence/next_due/etc.
    POST   /v1/recurring-bills/{tenant}/{id}/status      pause / resume / cancel
    POST   /v1/recurring-bills/{tenant}/jobs/run         spawn due pending expenses now (scheduler/backstop)
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from vula.commerce import recurring_bills as bills

log = logging.getLogger(__name__)
router = APIRouter(tags=["recurring-bills"])


class RecurringBillIn(BaseModel):
    description: str
    supplier: Optional[str] = None
    category: Optional[str] = None
    account_code: Optional[str] = None
    amount_cents: int
    cadence: str = "monthly"
    next_due: Optional[str] = None      # YYYY-MM-DD; defaults to one cadence from today
    lead_days: int = 7
    note: Optional[str] = None


@router.get("/{tenant_id}")
async def list_recurring_bills(tenant_id: str, status: Optional[str] = None) -> dict:
    return {"bills": await bills.list_bills(tenant_id, status=status)}


@router.post("/{tenant_id}")
async def create_recurring_bill(tenant_id: str, body: RecurringBillIn) -> dict:
    try:
        return await bills.create(tenant_id, body.model_dump())
    except Exception as exc:
        return {"error": f"{exc} (run migration 062?)"}


class PatchIn(BaseModel):
    description: Optional[str] = None
    supplier: Optional[str] = None
    category: Optional[str] = None
    account_code: Optional[str] = None
    amount_cents: Optional[int] = None
    cadence: Optional[str] = None
    next_due: Optional[str] = None
    lead_days: Optional[int] = None
    note: Optional[str] = None


@router.patch("/{tenant_id}/{bill_id}")
async def update_recurring_bill(tenant_id: str, bill_id: str, body: PatchIn) -> dict:
    patch: dict[str, Any] = {k: v for k, v in body.model_dump().items() if v is not None}
    return {"bill": await bills.update(tenant_id, bill_id, patch)}


class StatusIn(BaseModel):
    status: str


@router.post("/{tenant_id}/{bill_id}/status")
async def set_status(tenant_id: str, bill_id: str, body: StatusIn) -> dict:
    try:
        return {"bill": await bills.set_status(tenant_id, bill_id, body.status)}
    except ValueError as exc:
        return {"error": str(exc)}


@router.post("/{tenant_id}/jobs/run")
async def run_due(tenant_id: str) -> dict:
    return {"spawned": await bills.process_due(tenant_id)}
