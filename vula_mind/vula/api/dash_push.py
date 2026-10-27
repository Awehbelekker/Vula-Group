"""
vula/api/dash_push.py — turn the dashboard's notifications on or off on this device
(2026-10-27, PWA pass 3; vula/dash_push.py sends them).

    GET    /v1/push/{tenant_id}/key          the VAPID public key, and the alerts this person gets
    POST   /v1/push/{tenant_id}/subscribe    {endpoint, keys: {p256dh, auth}} — this device on
    POST   /v1/push/{tenant_id}/unsubscribe  {endpoint} — this device off
    POST   /v1/push/{tenant_id}/test         {endpoint} — one test notification to this device

Behind the tenant guard (server._TENANT_GUARD_RES); the person is read from their verified login
(tenant_auth.member_scope), never from the request body.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from config import settings

router = APIRouter(tags=["dashboard-push"])

# The alerts as people read them (vula/integrations/notify.py EVENTS + orders and approvals).
LABELS = {
    "new_order": "New orders", "payment_received": "Payments received", "new_invoice": "New invoices",
    "low_stock": "Stock running low", "customer_unanswered": "A customer waiting for a person",
    "which_project": "Documents needing a project", "followup_digest": "Emails waiting for a reply",
    "procurement_done": "Stock orders placed", "project_margin": "Projects below their fee",
    "bank_details_changed": "Changed bank details on an invoice",
}


async def _scope(tenant_id: str, authorization: str) -> dict:
    from vula.api.tenant_auth import member_scope
    scope = await member_scope(authorization, tenant_id)
    if scope is None:
        if not (authorization or "").removeprefix("Bearer ").strip():
            raise HTTPException(status_code=401, detail="Sign in required.")
        raise HTTPException(status_code=403, detail="You don't have access to this workspace.")
    return scope


class Keys(BaseModel):
    p256dh: str
    auth: str


class Subscription(BaseModel):
    endpoint: str
    keys: Optional[Keys] = None


@router.get("/{tenant_id}/key")
async def push_key(tenant_id: str, authorization: str = Header(default="")) -> dict:
    scope = await _scope(tenant_id, authorization)
    member = scope.get("member") or {}
    events = list(member.get("notify") or []) if member else []
    role = (scope.get("role") or member.get("role") or "").lower()
    gets = [LABELS[e] for e in events if e in LABELS]
    if role in ("owner", "manager", "operations"):        # vula/api/yoco.py _tenant_team
        gets.insert(0, "New paid orders")
    if role in ("owner", "manager"):                      # order approvals' approvers
        gets.append("Approvals waiting for you")
    return {"public_key": settings.vapid_public_key or None,
            "enabled": bool(settings.vapid_public_key and settings.vapid_private_key),
            "alerts": list(dict.fromkeys(gets)), "has_whatsapp": bool(member.get("whatsapp"))}


@router.post("/{tenant_id}/subscribe")
async def subscribe(tenant_id: str, body: Subscription, request: Request,
                    authorization: str = Header(default="")) -> dict:
    scope = await _scope(tenant_id, authorization)
    if not body.keys or not body.endpoint.startswith("https://"):
        raise HTTPException(status_code=422, detail="That isn't a push subscription from a browser.")
    from vula import dash_push
    try:
        dash_push.save_device(tenant_id, body.model_dump(), scope, request.headers.get("user-agent", ""))
    except Exception:
        raise HTTPException(status_code=503, detail="Couldn't turn notifications on just now — try again in a minute.")
    return {"ok": True}


@router.post("/{tenant_id}/unsubscribe")
async def unsubscribe(tenant_id: str, body: Subscription, authorization: str = Header(default="")) -> dict:
    await _scope(tenant_id, authorization)
    from vula import dash_push
    try:
        dash_push.forget_device(tenant_id, body.endpoint)
    except Exception:
        pass
    return {"ok": True}


@router.post("/{tenant_id}/test")
async def test_push(tenant_id: str, body: Subscription, authorization: str = Header(default="")) -> dict:
    await _scope(tenant_id, authorization)
    from vula import dash_push
    sub = next((s for s in dash_push.subscriptions(tenant_id) if s.get("endpoint") == body.endpoint), None)
    pusher = dash_push._pusher()
    if not sub or not pusher:
        raise HTTPException(status_code=404, detail="Notifications aren't on for this device.")
    outcome = await pusher.send(sub, {"title": "Notifications are on", "body": "This is how Vula will tell you about new orders and customers waiting.",
                                      "tag": "vula-test", "url": "/"})
    dash_push._record(sub, outcome)
    return {"sent": outcome == "ok", "outcome": outcome}
