"""HTTP + WhatsApp entry points for tap-to-pay.

Public (customer-facing, unauthenticated by design — protected by one-time tokens):
    GET /t/{code}                          NFC/QR tap -> 302 wa.me/<number>?text=PAY <token>
    GET /v1/tap/pay/{session}/{nonce}      short one-time link -> hosted checkout
    GET /v1/tap/done/{session}, /cancelled/{session}   informational return pages (never mark paid)
Merchant (tenant-guarded in server.py via _TENANT_GUARD_RES):
    POST /v1/tap/{tenant}/bills            create a bill
    POST /v1/tap/{tenant}/bills/{id}/cancel | /release
Setup (merchant, tenant-guarded) — see vula/tap/setup.py:
    GET  /v1/tap/{tenant}/setup | PUT /v1/tap/{tenant}/setup/shares
    POST /v1/tap/{tenant}/staff/{member}/tag[?rotate=1] | /test/{member} | /go-live | /pause
    GET  /v1/tap/{tenant}/bills            recent bills
Public: GET /t/{code}/qr.svg (QR of the public tap link).
Everything is OFF until the tenant's kb_settings.mode is testing/live (or the tenant is listed in
the operator override settings.tap_to_pay_tenants).
"""
from __future__ import annotations

import hashlib
import io
import logging
import time
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from config import settings
from vula.api.tenant_auth import require_tenant_actor
from vula.tap.core.money import MoneyError
from vula.tap.service import ReminderError, TapConfig, TapService
from vula.tap.setup import Setup, SetupError

log = logging.getLogger(__name__)
router = APIRouter(tags=["tap"])


_MODE_TTL = 30.0
_mode_cache: dict[str, tuple[float, str]] = {}


def _override_tenants() -> set[str]:
    return {t.strip() for t in (settings.tap_to_pay_tenants or "").split(",") if t.strip()}


def tenant_mode(tenant_id: Optional[str]) -> str:
    """off | testing | live. The env list is an operator override (= live); otherwise the tenant's
    kb_settings row, cached for 30 s (this runs on every inbound WhatsApp message). Any DB trouble
    reads as 'off' — failing closed never takes the normal assistant down."""
    if not tenant_id:
        return "off"
    if tenant_id in _override_tenants():
        return "live"
    now = time.monotonic()
    hit = _mode_cache.get(tenant_id)
    if hit and hit[0] > now:
        return hit[1]
    try:
        row = get_service().repo.get_settings(tenant_id) or {}
        mode, ttl = row.get("mode", "off"), _MODE_TTL
    except Exception as exc:  # noqa: BLE001
        log.debug("tap mode lookup failed for %s: %s", tenant_id, exc)
        mode, ttl = "off", 5.0
    _mode_cache[tenant_id] = (now + ttl, mode)
    return mode


def invalidate_mode(tenant_id: str) -> None:
    _mode_cache.pop(tenant_id, None)


def tenant_enabled(tenant_id: Optional[str]) -> bool:
    return tenant_mode(tenant_id) in ("testing", "live")


@lru_cache(maxsize=1)
def get_service() -> TapService:
    from vula.email_imap.credentials import decrypt_secret, encrypt_secret
    from vula.tap.adapters import PayFastGateway, WhatsAppMessenger
    from vula.tap.repo import SupabaseRepo
    pepper = settings.tap_hash_pepper or hashlib.sha256(
        ("tap:" + (settings.supabase_service_role_key or settings.supabase_service_key or "")).encode()
    ).hexdigest()
    cfg = TapConfig(pepper=pepper, public_base_url=settings.public_base_url,
                    encrypt=encrypt_secret, decrypt=decrypt_secret, enabled=tenant_enabled,
                    receipt_secret="receipt:" + pepper, receipt_base_url=settings.dashboard_url,
                    reminder_template=settings.tap_reminder_template,
                    reminder_final_template=settings.tap_reminder_final_template)
    from vula.tap.push import build_pusher
    return TapService(SupabaseRepo(), WhatsAppMessenger(), PayFastGateway(settings.public_base_url), cfg,
                      pusher=build_pusher(settings))


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
    url = get_service().tap(code[:64])
    if not url:
        return _page("Tag not verified", "We couldn't verify this tag. Ask staff to take payment another way.", 404)
    return RedirectResponse(url, status_code=302)


@router.get("/t/{code}/qr.svg")
async def tap_qr(code: str):
    """QR of the public tap link, for printing. Carries no secret (the link itself is public)."""
    tag = get_service().repo.get_tag_by_code(code[:64])
    if not tag or tag.get("status") != "active":
        raise HTTPException(status_code=404, detail="Unknown tag.")
    import segno
    buf = io.BytesIO()
    segno.make(f"{settings.public_base_url.rstrip('/')}/t/{code}", error="m").save(
        buf, kind="svg", scale=8, border=2, xmldecl=False)
    return Response(buf.getvalue(), media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=3600"})


@router.get("/v1/tap/pay/{session_id}/{nonce}")
async def pay(session_id: str, nonce: str):
    url = await get_service().open_pay_link(session_id[:64], nonce[:64])
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
    staff_id: Optional[str] = None         # who served: the bill goes on this person's tag
    tag_id: Optional[str] = None           # or name a till/table tag directly
    amount_cents: int = Field(gt=0, le=10_000_000)
    description: str = Field(min_length=1, max_length=120)
    customer_phone: Optional[str] = None


def _guard(tenant: str) -> TapService:
    if not tenant_enabled(tenant):
        raise HTTPException(status_code=409, detail="Tap to Pay is switched off. Run the test payment to switch it on.")
    return get_service()


@router.post("/v1/tap/{tenant}/bills")
async def create_bill(tenant: str, body: BillIn, identity: dict = Depends(require_tenant_actor)) -> dict:
    svc = _guard(tenant)
    tags = [t for t in svc.repo.list_tags(tenant) if t.get("status") == "active"]
    if body.tag_id:
        tag = next((t for t in tags if t["id"] == body.tag_id), None)
    elif body.staff_id:
        tag = next((t for t in tags if t.get("bound_id") == body.staff_id), None)
    else:
        raise HTTPException(status_code=422, detail="Choose who served this customer.")
    if not tag:
        raise HTTPException(status_code=404, detail="That person doesn't have a tag yet. Create one in setup.")
    try:
        bill = svc.create_bill(tenant_id=tenant, tag_id=tag["id"], amount_cents=body.amount_cents,
                               description=body.description, staff_id=body.staff_id or tag.get("bound_id"),
                               created_by=identity.get("email") or identity.get("user_id") or "",
                               customer_phone=body.customer_phone)
    except MoneyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        if "23505" in str(exc) or "duplicate key" in str(exc).lower():
            raise HTTPException(status_code=409, detail="This person already has an open bill.") from exc
        raise
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


# ── setup (self-serve) ───────────────────────────────────────────────────────────────────────
def _payfast_lookup(tenant_id: str) -> Optional[dict]:
    from vula.tap.adapters import _payfast_row
    row = _payfast_row(tenant_id)
    if not row:
        return {"connected": False, "mode": None}
    c = row["creds"] or {}
    return {"connected": bool(c.get("merchant_id") and c.get("merchant_key")), "mode": row["mode"]}


def _setup() -> Setup:
    return Setup(get_service(), settings.public_base_url.rstrip("/"), _payfast_lookup,
                 on_mode_change=invalidate_mode)


def _setup_call(fn, *a, **kw):
    try:
        return fn(*a, **kw)
    except SetupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/v1/tap/{tenant}/setup")
async def setup_status(tenant: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    return _setup().status(tenant)


class SharesIn(BaseModel):
    default_share_bp: int = Field(ge=0, le=10_000)
    staff_shares: dict[str, int] = {}


@router.put("/v1/tap/{tenant}/setup/shares")
async def setup_shares(tenant: str, body: SharesIn, identity: dict = Depends(require_tenant_actor)) -> dict:
    _setup_call(_setup().save_shares, tenant, body.default_share_bp, body.staff_shares)
    return _setup().status(tenant)


@router.post("/v1/tap/{tenant}/staff/{member_id}/tag")
async def setup_tag(tenant: str, member_id: str, rotate: bool = False,
                    identity: dict = Depends(require_tenant_actor)) -> dict:
    return _setup_call(_setup().ensure_tag, tenant, member_id, rotate)


@router.post("/v1/tap/{tenant}/test/{member_id}")
async def setup_test(tenant: str, member_id: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    return _setup_call(_setup().start_test, tenant, member_id)


@router.post("/v1/tap/{tenant}/go-live")
async def setup_go_live(tenant: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    _setup_call(_setup().go_live, tenant)
    return _setup().status(tenant)


@router.post("/v1/tap/{tenant}/pause")
async def setup_pause(tenant: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    _setup_call(_setup().pause, tenant)
    return _setup().status(tenant)


@router.get("/v1/tap/{tenant}/bills")
async def list_bills(tenant: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    return {"bills": get_service().repo.recent_bills(tenant, 20)}


# ── coach devices (owner side) ───────────────────────────────────────────────────────────────
def _auth():
    from vula.tap.appauth import AppAuth  # noqa: F401
    from vula.tap.appapi import get_auth
    return get_auth()


@router.post("/v1/tap/{tenant}/members/{member_id}/enrol-code")
async def enrol_code(tenant: str, member_id: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    from vula.tap.appauth import AuthError
    try:
        r = _auth().issue_enrol_code(tenant, member_id, identity.get("email") or identity.get("user_id") or "")
    except AuthError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.message) from exc
    return {**r, "app_url": f"{settings.dashboard_url.rstrip('/')}/pay/?t={tenant}"}


@router.get("/v1/tap/{tenant}/devices")
async def list_devices(tenant: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    return {"devices": get_service().repo.list_devices(tenant)}


@router.post("/v1/tap/{tenant}/devices/{device_id}/revoke")
async def revoke_device(tenant: str, device_id: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    _auth().revoke_device(tenant, device_id)
    return {"revoked": True}


# ── receipts ─────────────────────────────────────────────────────────────────────────────────
def build_receipt(svc: TapService, src: dict) -> dict:
    """The public, customer-safe receipt: no phone numbers, no staff share, no other payments."""
    from vula.tap.receipt import vat_included
    tenant = src["tenant_id"]
    vat = svc.repo.merchant_vat(tenant)
    first = (svc.repo.staff_name(tenant, src.get("staff_id")) or None)
    return {"merchant": svc.repo.merchant_name(tenant), "description": src.get("description") or "Payment",
            "served_by": first, "bill_cents": src["bill_cents"], "tip_cents": src["tip_cents"],
            "total_cents": src["bill_cents"] + src["tip_cents"], "currency": "ZAR",
            "vat_number": vat["vat_number"] if vat["vat_registered"] else None,
            "vat_cents": vat_included(src["bill_cents"]) if vat["vat_registered"] and vat["vat_number"] else None,
            "paid_at": src["paid_at"], "ref": str(src["payment_id"])[:8].upper(), "is_test": src["is_test"],
            **_tax_fields(svc, src)}


def _tax_fields(svc: TapService, src: dict) -> dict:
    from vula.tap.core.taxinvoice import format_number
    tenant, desk = src["tenant_id"], svc.tax
    inv = svc.repo.get_tax_invoice(tenant, src["payment_id"])
    return {"tax_invoice_number": format_number(inv["number"]) if inv else None,
            "can_tax_invoice": bool(inv) or (desk.can_issue(tenant) and src["bill_cents"] > 0 and not src["is_test"])}


@router.get("/v1/tap/receipt/{token}")
async def get_receipt(token: str) -> Response:
    from vula.tap.receipt import read_token
    svc = get_service()
    parsed = read_token(svc.cfg.receipt_secret, token[:300])
    src = svc.repo.receipt_source(parsed[0]) if parsed else None
    if not src or src["nonce"] != parsed[1] or src.get("revoked_at"):
        raise HTTPException(status_code=404, detail="This receipt link is no longer available.")
    import json
    return Response(json.dumps(build_receipt(svc, src)), media_type="application/json",
                    headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})


def _valid_receipt(svc: TapService, token: str) -> dict:
    from vula.tap.receipt import read_token
    parsed = read_token(svc.cfg.receipt_secret, token[:300])
    src = svc.repo.receipt_source(parsed[0]) if parsed else None
    if not src or src["nonce"] != parsed[1] or src.get("revoked_at"):
        raise HTTPException(status_code=404, detail="This receipt link is no longer available.")
    return src


class TaxIn(BaseModel):
    company: str = Field(min_length=2, max_length=120)
    vat: str = Field(min_length=9, max_length=14)
    address: Optional[str] = Field(default=None, max_length=200)


@router.post("/v1/tap/receipt/{token}/tax-invoice")
async def issue_tax_invoice(token: str, body: TaxIn) -> dict:
    from vula.tap.core.taxinvoice import format_number
    from vula.tap.tax import TaxError
    svc = get_service()
    src = _valid_receipt(svc, token)
    try:
        inv, created = svc.tax.issue(src["payment_id"], body.company, body.vat, body.address or "")
    except TaxError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"number": format_number(inv["number"]), "created": created,
            "pdf_url": f"/v1/tap/receipt/{token}/tax-invoice.pdf"}


@router.get("/v1/tap/receipt/{token}/tax-invoice.pdf")
async def tax_invoice_pdf(token: str) -> Response:
    import asyncio
    from vula.tap.core.taxinvoice import format_number
    svc = get_service()
    src = _valid_receipt(svc, token)
    inv = svc.repo.get_tax_invoice(src["tenant_id"], src["payment_id"])
    if not inv:
        raise HTTPException(status_code=404, detail="No tax invoice has been requested for this payment.")
    pdf = await asyncio.to_thread(svc.tax.render_pdf, inv)
    return Response(pdf, media_type="application/pdf",
                    headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex",
                             "Content-Disposition": f'inline; filename="Tax-Invoice-{format_number(inv["number"])}.pdf"'})


@router.post("/v1/tap/{tenant}/payments/{payment_id}/receipt/revoke")
async def revoke_receipt(tenant: str, payment_id: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    src = get_service().repo.receipt_source(payment_id)
    if not src or src["tenant_id"] != tenant:
        raise HTTPException(status_code=404, detail="Payment not found.")
    get_service().repo.revoke_receipt(tenant, payment_id)
    return {"revoked": True}


# ── unpaid bills & reminders (owner) ─────────────────────────────────────────────────────────
@router.get("/v1/tap/{tenant}/unpaid")
async def unpaid(tenant: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    svc = get_service()
    return {"unpaid": svc.unpaid(tenant), "reminders_max": svc._reminders_max(tenant)}


class RemindersIn(BaseModel):
    max: int = Field(ge=0, le=3)


@router.put("/v1/tap/{tenant}/setup/reminders")
async def set_reminders(tenant: str, body: RemindersIn, identity: dict = Depends(require_tenant_actor)) -> dict:
    get_service().repo.upsert_settings(tenant, {"reminders_max": body.max})
    return {"reminders_max": body.max}


@router.post("/v1/tap/{tenant}/bills/{bill_id}/remind")
async def remind_now(tenant: str, bill_id: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    try:
        await _guard(tenant).send_reminder_now(tenant, bill_id)
    except ReminderError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"sent": True}


class CloseIn(BaseModel):
    action: str = Field(pattern="^(paid_other|write_off|release)$")
    reason: Optional[str] = Field(default=None, max_length=20)


@router.post("/v1/tap/{tenant}/bills/{bill_id}/close")
async def close_unpaid(tenant: str, bill_id: str, body: CloseIn, identity: dict = Depends(require_tenant_actor)) -> dict:
    try:
        ok = get_service().close_unpaid(tenant, bill_id, body.action, body.reason)
    except ReminderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not ok:
        raise HTTPException(status_code=409, detail="This bill isn't waiting on a payment any more.")
    return {"closed": body.action}
