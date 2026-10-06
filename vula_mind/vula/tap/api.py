"""HTTP + WhatsApp entry points for tap-to-pay.

Public (customer-facing, unauthenticated by design — protected by one-time tokens):
    GET /t/{code}                          NFC/QR tap -> 302 wa.me/<number>?text=PAY <token>
    GET /v1/tap/pay/{session}/{nonce}      short one-time link -> hosted checkout
    GET /v1/tap/done/{session}, /cancelled/{session}   informational return pages (never mark paid)
Merchant (tenant-guarded in server.py via _TENANT_GUARD_RES):
    POST /v1/tap/{tenant}/bills            create a bill
    POST /v1/tap/{tenant}/bills/{id}/cancel | /release
Everything is OFF unless the tenant is listed in settings.tap_to_pay_tenants.
"""
from __future__ import annotations

import hashlib
import logging
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from config import settings
from vula.api.tenant_auth import require_tenant_actor
from vula.tap.core.money import MoneyError
from vula.tap.service import TapConfig, TapService

log = logging.getLogger(__name__)
router = APIRouter(tags=["tap"])


def tenant_enabled(tenant_id: Optional[str]) -> bool:
    allowed = {t.strip() for t in (settings.tap_to_pay_tenants or "").split(",") if t.strip()}
    return bool(tenant_id) and tenant_id in allowed


@lru_cache(maxsize=1)
def get_service() -> TapService:
    from vula.email_imap.credentials import decrypt_secret, encrypt_secret
    from vula.tap.adapters import PayFastGateway, WhatsAppMessenger
    from vula.tap.repo import SupabaseRepo
    pepper = settings.tap_hash_pepper or hashlib.sha256(
        ("tap:" + (settings.supabase_service_role_key or settings.supabase_service_key or "")).encode()
    ).hexdigest()
    cfg = TapConfig(pepper=pepper, public_base_url=settings.public_base_url,
                    encrypt=encrypt_secret, decrypt=decrypt_secret)
    return TapService(SupabaseRepo(), WhatsAppMessenger(), PayFastGateway(settings.public_base_url), cfg)


# ── WhatsApp hooks (called from vula/api/whatsapp.py) ────────────────────────────────────────
async def try_handle_text(tenant_id: Optional[str], phone: str, text: str) -> bool:
    if not tenant_enabled(tenant_id):
        return False
    try:
        return await get_service().handle_text(tenant_id, phone, text)
    except Exception:  # noqa: BLE001 — never let a tap bug take the normal assistant down
        log.exception("tap text handler failed (tenant=%s)", tenant_id)
        return False


async def try_handle_interactive(tenant_id: Optional[str], phone: str, reply_id: str) -> bool:
    if not tenant_enabled(tenant_id) or not reply_id.startswith("kb:"):
        return False
    try:
        return await get_service().handle_interactive(tenant_id, phone, reply_id)
    except Exception:  # noqa: BLE001
        log.exception("tap interactive handler failed (tenant=%s)", tenant_id)
        return True


async def try_handle_itn(tenant_id: str, headers: dict, body: bytes, form: dict) -> Optional[str]:
    """PayFast ITN for a tap session (m_payment_id 'kb-<session>'). None = not a tap payment."""
    if not str(form.get("m_payment_id", "")).startswith("kb-") or not tenant_enabled(tenant_id):
        return None
    try:
        return await get_service().confirm_payment(tenant_id, headers, body, form)
    except Exception:  # noqa: BLE001
        log.exception("tap ITN handling failed (tenant=%s)", tenant_id)
        return "error"


# ── public pages ─────────────────────────────────────────────────────────────────────────────
def _page(title: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title><body style='font-family:system-ui;max-width:28rem;margin:4rem auto;padding:0 1rem'>"
        f"<h2>{title}</h2><p>{body}</p>", status_code=status)


@router.get("/t/{code}")
async def tap(code: str):
    url = get_service().tap(code[:64]) if settings.tap_to_pay_tenants else None
    if not url:
        return _page("Tag not verified", "We couldn't verify this tag. Ask staff to take payment another way.", 404)
    return RedirectResponse(url, status_code=302)


@router.get("/v1/tap/pay/{session_id}/{nonce}")
async def pay(session_id: str, nonce: str):
    url = await get_service().open_pay_link(session_id[:64], nonce[:64]) if settings.tap_to_pay_tenants else None
    if not url:
        return _page("Link expired", "This payment link has expired. Tap the tag again to restart.", 410)
    return RedirectResponse(url, status_code=303)


@router.get("/v1/tap/done/{session_id}")
async def done(session_id: str):
    return _page("Thank you", "Your slip will arrive in WhatsApp as soon as the payment is confirmed. "
                              "You can close this page.")


@router.get("/v1/tap/cancelled/{session_id}")
async def cancelled(session_id: str):
    return _page("Payment cancelled", "You haven't been charged. Tap the tag again to restart.")


# ── merchant endpoints ───────────────────────────────────────────────────────────────────────
class BillIn(BaseModel):
    tag_id: str
    amount_cents: int = Field(gt=0, le=10_000_000)
    description: str = Field(min_length=1, max_length=120)
    staff_id: Optional[str] = None
    customer_phone: Optional[str] = None


def _guard(tenant: str) -> TapService:
    if not tenant_enabled(tenant):
        raise HTTPException(status_code=404, detail="Tap-to-pay is not enabled for this workspace.")
    return get_service()


@router.post("/v1/tap/{tenant}/bills")
async def create_bill(tenant: str, body: BillIn, identity: dict = Depends(require_tenant_actor)) -> dict:
    svc = _guard(tenant)
    tag = svc.repo.get_tag(tenant, body.tag_id)
    if not tag or tag.get("status") != "active":
        raise HTTPException(status_code=404, detail="Unknown or disabled tag.")
    try:
        bill = svc.create_bill(tenant_id=tenant, tag_id=body.tag_id, amount_cents=body.amount_cents,
                               description=body.description, staff_id=body.staff_id,
                               created_by=identity.get("email") or identity.get("user_id") or "",
                               customer_phone=body.customer_phone)
    except MoneyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # bill_code is returned ONCE so the merchant can hand it to a customer paying from another number
    return {"id": bill["id"], "status": bill["status"], "bill_code": bill.get("bill_code")}


@router.post("/v1/tap/{tenant}/bills/{bill_id}/cancel")
async def cancel_bill(tenant: str, bill_id: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    if not _guard(tenant).cancel_bill(tenant, bill_id):
        raise HTTPException(status_code=409, detail="This bill can't be cancelled.")
    return {"status": "cancelled"}


@router.post("/v1/tap/{tenant}/bills/{bill_id}/release")
async def release_bill(tenant: str, bill_id: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    if not _guard(tenant).release_bill(tenant, bill_id):
        raise HTTPException(status_code=409, detail="This bill isn't claimed.")
    return {"status": "open"}
