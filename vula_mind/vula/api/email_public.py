"""
vula/api/email_public.py — public unsubscribe link for email campaigns.

Mounted at the app root (no /v1 prefix, no auth) — every campaign email includes this
link in its footer (vula/api/commerce.py::admin_send_email_campaign). Marks the address
opted out (commerce_email_consent, migration 106) so no future campaign reaches it —
required for POPIA/CAN-SPAM compliance, not optional, unlike the open/click tracking
this feature deliberately deferred.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

log = logging.getLogger(__name__)
router = APIRouter(tags=["email-public"])


def _client():
    from vula.commerce import service
    return service._client()


def _page(message: str, ok: bool = True) -> HTMLResponse:
    colour = "#2C5545" if ok else "#b91c1c"
    return HTMLResponse(
        f"<html><body style='font-family:system-ui;text-align:center;padding:48px;color:{colour}'>"
        f"<h2>{message}</h2></body></html>"
    )


@router.get("/email/unsubscribe")
async def unsubscribe(tenant: str = Query(...), email: str = Query(...)) -> HTMLResponse:
    tenant = (tenant or "").strip()
    addr = (email or "").strip().lower()
    if not tenant or not addr or "@" not in addr:
        return _page("Invalid unsubscribe link.", ok=False)
    try:
        _client().table("commerce_email_consent").upsert({
            "tenant_id": tenant, "email": addr, "status": "opted_out",
            "source": "unsubscribe_link", "updated_at": "now()",
        }, on_conflict="tenant_id,email").execute()
    except Exception as exc:
        log.warning("email unsubscribe failed for %s/%s: %s", tenant, addr, exc)
        return _page("Something went wrong — please try again.", ok=False)
    return _page(f"{addr} has been unsubscribed. You won't receive further emails from us.")
