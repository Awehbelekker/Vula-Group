"""
vula/api/email_public.py — public unsubscribe link for email campaigns.

Mounted at the app root (no /v1 prefix, no auth) — every campaign email includes this
link in its footer (vula/api/commerce.py::admin_send_email_campaign). Marks the address
opted out (commerce_email_consent, migration 106) so no future campaign reaches it —
required for POPIA/CAN-SPAM compliance, not optional, unlike the open/click tracking
this feature deliberately deferred.
"""
from __future__ import annotations

import hashlib
import hmac
import html
import logging
from urllib.parse import quote

from fastapi import APIRouter, Form, Query
from fastapi.responses import HTMLResponse

log = logging.getLogger(__name__)
router = APIRouter(tags=["email-public"])


def _client():
    from vula.commerce import service
    return service._client()


def _page(message: str, ok: bool = True, extra_html: str = "") -> HTMLResponse:
    colour = "#2C5545" if ok else "#b91c1c"
    return HTMLResponse(
        f"<html><body style='font-family:system-ui;text-align:center;padding:48px;color:{colour}'>"
        f"<h2>{html.escape(message)}</h2>{extra_html}</body></html>"
    )


def _key() -> bytes:
    from config import settings
    seed = (settings.supabase_service_role_key or settings.supabase_service_key or "vula-email")
    return hashlib.sha256(("email-unsubscribe:" + seed).encode()).digest()


def unsubscribe_token(tenant: str, email: str) -> str:
    msg = f"{tenant}|{(email or '').strip().lower()}".encode()
    return hmac.new(_key(), msg, hashlib.sha256).hexdigest()[:32]


def unsubscribe_url(base: str, tenant: str, email: str) -> str:
    """Signed one-click unsubscribe link for a campaign footer."""
    return (f"{base}/email/unsubscribe?tenant={quote(tenant)}&email={quote(email)}"
            f"&t={unsubscribe_token(tenant, email)}")


@router.get("/email/unsubscribe")
async def unsubscribe(tenant: str = Query(...), email: str = Query(...),
                      t: str = Query(default="")) -> HTMLResponse:
    """A signed link (t=HMAC of tenant+email) unsubscribes in one click. An unsigned link —
    anyone can type one for any address, and mail scanners pre-fetch links — only shows a
    confirm button (POST), so nobody is unsubscribed without a person actually clicking."""
    tenant = (tenant or "").strip()
    addr = (email or "").strip().lower()
    if not tenant or not addr or "@" not in addr:
        return _page("Invalid unsubscribe link.", ok=False)
    if not (t and hmac.compare_digest(t, unsubscribe_token(tenant, addr))):
        form = ("<form method='post' action='/email/unsubscribe'>"
                f"<input type='hidden' name='tenant' value='{html.escape(tenant, quote=True)}'>"
                f"<input type='hidden' name='email' value='{html.escape(addr, quote=True)}'>"
                "<button style='padding:10px 20px;font-size:16px'>Unsubscribe</button></form>")
        return _page(f"Unsubscribe {addr} from these emails?", extra_html=form)
    return _do_unsubscribe(tenant, addr)


@router.post("/email/unsubscribe")
async def unsubscribe_confirm(tenant: str = Form(...), email: str = Form(...)) -> HTMLResponse:
    tenant = (tenant or "").strip()
    addr = (email or "").strip().lower()
    if not tenant or not addr or "@" not in addr:
        return _page("Invalid unsubscribe link.", ok=False)
    return _do_unsubscribe(tenant, addr)


def _do_unsubscribe(tenant: str, addr: str) -> HTMLResponse:
    try:
        _client().table("commerce_email_consent").upsert({
            "tenant_id": tenant, "email": addr, "status": "opted_out",
            "source": "unsubscribe_link", "updated_at": "now()",
        }, on_conflict="tenant_id,email").execute()
    except Exception as exc:
        log.warning("email unsubscribe failed for %s/%s: %s", tenant, addr, exc)
        return _page("Something went wrong — please try again.", ok=False)
    return _page(f"{addr} has been unsubscribed. You won't receive further emails from us.")
