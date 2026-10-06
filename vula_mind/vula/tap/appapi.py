"""Coach app API (the /pay/ PWA). Own auth (device + PIN -> signed token), NOT the Supabase tenant
guard, so these paths are listed as public in the route sweep and every one except enrol/login
demands a valid app token. Staff see only their own bills; owners/managers see all.

    POST /v1/tap/app/enrol | /login
    GET  /v1/tap/app/me | /bills | /events (SSE) | /push-key
    POST /v1/tap/app/bills | /bills/{id}/cancel | /bills/{id}/release | /push ;  DELETE /push
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Awaitable, Callable, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from config import settings
from vula.tap import api as tap_api
from vula.tap.appauth import Actor, AppAuth, AuthError
from vula.tap.core.money import MoneyError

log = logging.getLogger(__name__)
router = APIRouter(tags=["tap-app"])


def get_auth() -> AppAuth:
    svc = tap_api.get_service()
    secret = settings.tap_hash_pepper or settings.supabase_service_role_key or settings.supabase_service_key or "dev"
    return AppAuth(svc.repo, "tapapp:" + secret, svc.cfg.clock)


def _http(exc: AuthError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


def app_actor(authorization: str = Header(default="")) -> Actor:
    try:
        return get_auth().actor(authorization.removeprefix("Bearer ").strip())
    except AuthError as exc:
        raise _http(exc) from exc


# ── sign-in ──────────────────────────────────────────────────────────────────────────────────
class EnrolIn(BaseModel):
    tenant: str = Field(max_length=80)
    phone: str = Field(max_length=32)
    code: str = Field(max_length=12)
    pin: str = Field(max_length=8)
    label: str = Field(default="", max_length=60)


class LoginIn(BaseModel):
    device_token: str = Field(max_length=200)
    pin: str = Field(max_length=8)


@router.post("/v1/tap/app/enrol")
async def enrol(body: EnrolIn) -> dict:
    if not tap_api.tenant_enabled(body.tenant):
        raise HTTPException(status_code=403, detail="Tap to Pay isn't switched on for this business yet.")
    try:
        return get_auth().enrol(body.tenant, body.phone, body.code, body.pin, body.label)
    except AuthError as exc:
        raise _http(exc) from exc


@router.post("/v1/tap/app/login")
async def login(body: LoginIn) -> dict:
    try:
        return get_auth().login(body.device_token, body.pin)
    except AuthError as exc:
        raise _http(exc) from exc


# ── read ─────────────────────────────────────────────────────────────────────────────────────
@router.get("/v1/tap/app/me")
async def me(a: Actor = Depends(app_actor)) -> dict:
    svc = tap_api.get_service()
    tag = next((t for t in svc.repo.list_tags(a.tenant_id)
                if t.get("bound_id") == a.member_id and t.get("status") == "active"), None)
    base = settings.public_base_url.rstrip("/")
    return {"name": a.name, "role": a.role, "sees_all": a.sees_all,
            "merchant": svc.repo.merchant_name(a.tenant_id), "mode": tap_api.tenant_mode(a.tenant_id),
            "tag": ({"url": f"{base}/t/{tag['code']}", "qr": f"{base}/t/{tag['code']}/qr.svg"} if tag else None),
            "push_public_key": settings.vapid_public_key or None}


def _public_bill(b: dict) -> dict:
    return {k: b.get(k) for k in ("id", "status", "description", "subtotal_cents", "staff_id", "is_test", "updated_at")}


@router.get("/v1/tap/app/bills")
async def my_bills(a: Actor = Depends(app_actor)) -> dict:
    svc = tap_api.get_service()
    rows = [b for b in svc.repo.recent_bills(a.tenant_id, 40) if a.sees_all or b.get("staff_id") == a.member_id][:20]
    out = []
    for b in rows:
        pb = _public_bill(b)
        if b["status"] == "paid" and not b.get("is_test"):
            pb.update(svc.repo.paid_detail(a.tenant_id, b["id"], b.get("staff_id")))
        out.append(pb)
    return {"bills": out, "server_time": datetime.now(timezone.utc).isoformat()}


# ── write ────────────────────────────────────────────────────────────────────────────────────
class NewBill(BaseModel):
    amount_cents: int = Field(gt=0, le=10_000_000)
    description: str = Field(min_length=1, max_length=120)
    customer_phone: Optional[str] = Field(default=None, max_length=32)
    staff_id: Optional[str] = None            # managers/owners may bill on someone else's tag


def _own_bill(a: Actor, bill_id: str) -> dict:
    b = tap_api.get_service().repo.get_bill(a.tenant_id, bill_id)
    if not b or not (a.sees_all or b.get("staff_id") == a.member_id):
        raise HTTPException(status_code=404, detail="Bill not found.")
    return b


@router.post("/v1/tap/app/bills")
async def new_bill(body: NewBill, a: Actor = Depends(app_actor)) -> dict:
    if not tap_api.tenant_enabled(a.tenant_id):
        raise HTTPException(status_code=409, detail="Tap to Pay is switched off. Ask your manager.")
    svc = tap_api.get_service()
    staff = body.staff_id if (body.staff_id and a.sees_all) else a.member_id
    tag = next((t for t in svc.repo.list_tags(a.tenant_id)
                if t.get("bound_id") == staff and t.get("status") == "active"), None)
    if not tag:
        raise HTTPException(status_code=404, detail="You don't have a tag yet. Ask your manager to create one.")
    try:
        bill = svc.create_bill(tenant_id=a.tenant_id, tag_id=tag["id"], amount_cents=body.amount_cents,
                               description=body.description, staff_id=staff, created_by=a.member_id,
                               customer_phone=body.customer_phone)
    except MoneyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        if "23505" in str(exc) or "duplicate key" in str(exc).lower():
            raise HTTPException(status_code=409, detail="You already have an open bill. Cancel it or wait for it to be paid.") from exc
        raise
    return {**_public_bill(bill), "bill_code": bill.get("bill_code")}


@router.post("/v1/tap/app/bills/{bill_id}/cancel")
async def cancel(bill_id: str, a: Actor = Depends(app_actor)) -> dict:
    _own_bill(a, bill_id)
    if not tap_api.get_service().cancel_bill(a.tenant_id, bill_id):
        raise HTTPException(status_code=409, detail="This bill can't be cancelled.")
    return {"status": "cancelled"}


@router.post("/v1/tap/app/bills/{bill_id}/release")
async def release(bill_id: str, a: Actor = Depends(app_actor)) -> dict:
    _own_bill(a, bill_id)
    if not tap_api.get_service().release_bill(a.tenant_id, bill_id):
        raise HTTPException(status_code=409, detail="This bill isn't claimed.")
    return {"status": "open"}


# ── live status (server-sent events, polled from the DB so it works across workers) ──────────
def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


async def event_stream(repo, a: Actor, since_iso: str, *, poll: float = 2.0, heartbeat: float = 20.0,
                       max_seconds: float = 600.0,
                       sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> AsyncIterator[str]:
    """Yields `bill` events for bills changed since the cursor. The client reconnects (and passes
    the last cursor) when the stream ends; ending after max_seconds keeps connections from piling up."""
    staff_filter = None if a.sees_all else a.member_id
    cursor, waited, since_beat = since_iso, 0.0, 0.0
    yield _sse("hello", {"cursor": cursor})
    while waited < max_seconds:
        for b in repo.bill_updates(a.tenant_id, cursor, staff_filter):
            pb = _public_bill(b)
            if b["status"] == "paid" and not b.get("is_test"):
                pb.update(repo.paid_detail(a.tenant_id, b["id"], b.get("staff_id")))
            cursor = max(cursor, str(b["updated_at"]))
            yield _sse("bill", {**pb, "cursor": cursor})
            since_beat = 0.0
        if since_beat >= heartbeat:
            yield ": keep-alive\n\n"
            since_beat = 0.0
        await sleep(poll)
        waited += poll
        since_beat += poll
    yield _sse("bye", {"cursor": cursor})


@router.get("/v1/tap/app/events")
async def events(since: Optional[str] = None, a: Actor = Depends(app_actor)):
    svc = tap_api.get_service()
    start = since or (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    return StreamingResponse(event_stream(svc.repo, a, start), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── push ─────────────────────────────────────────────────────────────────────────────────────
class PushKeys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class PushSub(BaseModel):
    endpoint: str = Field(max_length=600)
    keys: PushKeys


@router.get("/v1/tap/app/push-key")
async def push_key(a: Actor = Depends(app_actor)) -> dict:
    return {"public_key": settings.vapid_public_key or None}


@router.post("/v1/tap/app/push")
async def push_subscribe(body: PushSub, a: Actor = Depends(app_actor)) -> dict:
    if not body.endpoint.startswith("https://"):
        raise HTTPException(status_code=422, detail="Bad subscription.")
    tap_api.get_service().repo.upsert_push_sub({
        "tenant_id": a.tenant_id, "member_id": a.member_id, "device_id": a.device_id,
        "endpoint": body.endpoint, "p256dh": body.keys.p256dh, "auth": body.keys.auth})
    return {"subscribed": True}


class PushOff(BaseModel):
    endpoint: str = Field(max_length=600)


@router.delete("/v1/tap/app/push")
async def push_unsubscribe(body: PushOff, a: Actor = Depends(app_actor)) -> dict:
    repo = tap_api.get_service().repo
    # only remove a subscription that belongs to this person
    if any(s["endpoint"] == body.endpoint for s in repo.push_subs_for(a.tenant_id, a.member_id)):
        repo.delete_push_sub(body.endpoint)
    return {"subscribed": False}
