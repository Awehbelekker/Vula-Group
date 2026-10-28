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
import secrets
import time
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
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
    from vula.tap.gateways import GatewayRouter, tenant_choice
    from vula.tap.push import build_pusher
    gateways = {"payfast": PayFastGateway(settings.public_base_url)}
    from vula.tap.gateways import CHOICE_TTL_S
    router = GatewayRouter(gateways, choose=lambda t: tenant_choice(t, set(gateways)), ttl_s=CHOICE_TTL_S)
    return TapService(SupabaseRepo(), WhatsAppMessenger(), router, cfg, pusher=build_pusher(settings))


# Gateways Tap to Pay can check out through (vula/tap/gateways.py). A payment notification from
# one of these is offered to Tap to Pay first; anything that isn't a tap payment carries on.
TAP_GATEWAYS = ("payfast",)


# ── WhatsApp hooks (called from vula/api/whatsapp.py) ────────────────────────────────────────
async def try_handle_text(tenant_id: Optional[str], phone: str, text: str, name: str = "") -> bool:
    if not tenant_enabled(tenant_id):
        return False
    try:
        return await get_service().handle_text(tenant_id, phone, text, name)
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


async def try_handle_itn(tenant_id: str, headers: dict, body: bytes, form: dict,
                         provider: str = "payfast") -> Optional[str]:
    """A gateway's payment notification for a tap session (reference 'kb-<session>'). None = not a
    tap payment, so the generic invoice/order handling carries on."""
    if provider not in TAP_GATEWAYS or not tenant_enabled(tenant_id):
        return None
    if provider == "payfast" and not str(form.get("m_payment_id", "")).startswith("kb-"):
        return None
    try:
        outcome = await get_service().confirm_payment(tenant_id, headers, body, form, provider=provider)
    except Exception:  # noqa: BLE001
        log.exception("tap ITN handling failed (tenant=%s)", tenant_id)
        return "error"
    return None if outcome == "not_tap" else outcome


# ── public pages ─────────────────────────────────────────────────────────────────────────────
# Pay pages: no framing, no third-party scripts (inline ones carry a per-response nonce), forms may
# only go here or to PayFast (the Pay button 303s to PayFast's checkout, which form-action covers).
_PAYFAST_ORIGINS = "https://*.payfast.co.za https://*.payfast.io"


def _secure(resp: Response, nonce: str = "") -> Response:
    script = f"'nonce-{nonce}'" if nonce else "'none'"
    resp.headers["Content-Security-Policy"] = (
        f"default-src 'none'; script-src {script}; style-src 'unsafe-inline'; img-src 'self' data:; "
        f"connect-src 'self'; form-action 'self' {_PAYFAST_ORIGINS}; frame-ancestors 'none'; base-uri 'none'")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    resp.headers.setdefault("Cache-Control", "no-store")
    resp.headers["X-Robots-Tag"] = "noindex"
    return resp


def _page(title: str, body: str, status: int = 200) -> Response:
    return _secure(HTMLResponse(
        f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>{title}</title><body style='font-family:system-ui;max-width:28rem;margin:4rem auto;padding:0 1rem'>"
        f"<h2>{title}</h2><p>{body}</p>", status_code=status))


@router.get("/t/{code}")
async def tap(code: str, request: Request, via: str = ""):
    code = code[:64]
    if settings.tap_express and via != "whatsapp":
        return _express(code, request)
    url = get_service().tap(code)
    if not url:
        return _page("Tag not verified", "We couldn't verify this tag. Ask staff to take payment another way.", 404)
    return RedirectResponse(url, status_code=302)


def _express(code: str, request: Request, error: str = "") -> Response:
    """Express pay (vula/tap/express.py): the tap opens one pay page instead of WhatsApp."""
    from vula.tap import express_page
    from vula.tap.express import COOKIE, ExpressPay
    svc = get_service()
    cookie = request.cookies.get(COOKIE) or ""
    fresh = not cookie
    cookie = cookie or ExpressPay.new_cookie()
    v = svc.express.view(code, cookie)
    if v is None:
        return _page("Tag not verified", "We couldn't verify this tag. Ask staff to take payment another way.", 404)
    if v["kind"] == "whatsapp":                    # a bill sent to a number: WhatsApp proves the number
        url = svc.tap(code)
        return RedirectResponse(url, status_code=302) if url else _page(
            "Tag not verified", "We couldn't verify this tag. Ask staff to take payment another way.", 404)
    nonce = secrets.token_urlsafe(16)
    resp = _secure(HTMLResponse(express_page.render(code, v, error, nonce), status_code=200), nonce)
    if fresh:
        resp.set_cookie(COOKIE, cookie, max_age=86400, httponly=True, samesite="lax", secure=True)
    return resp


@router.post("/t/{code}/pay")
async def tap_pay(code: str, request: Request):
    """The express page's Pay button: claim, price, then straight to PayFast."""
    from vula.tap.express import COOKIE, ExpressError
    code = code[:64]
    form = await request.form()
    cookie = request.cookies.get(COOKIE) or ""
    if not cookie:                                  # cookies blocked: the page can't know who is paying
        return _express(code, request, "Please allow cookies for this page, or pay in WhatsApp below.")
    svc = get_service()
    quick = str(form.get("amount") or "")
    amount = str(form.get("amount_other") or "") if quick == "other" else quick
    if quick.isdigit() and int(quick) in svc.cfg.quick_tip_presets_cents:   # a preset button, in cents
        amount = f"{int(quick) / 100:.2f}"
    try:
        url = await svc.express.checkout(code, str(form.get("token") or ""), cookie,
                                         tip=str(form.get("tip") or "0"),
                                         custom_tip=str(form.get("custom_tip") or ""), amount=amount)
    except ExpressError as exc:
        return _express(code, request, str(exc))
    except MoneyError:
        return _express(code, request, "That amount doesn't look right. Type it in rands, for example 15.")
    return RedirectResponse(url, status_code=303)


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
async def done(session_id: str, request: Request):
    from vula.tap import express_page
    from vula.tap.express import COOKIE
    svc = get_service()
    slip = svc.express.slip_link(session_id[:64], request.cookies.get(COOKIE) or "")
    if slip:                # an express payment: wait for the gateway, then print the slip on screen
        s = svc.repo.get_session_any_tenant(session_id[:64]) or {}
        nonce = secrets.token_urlsafe(16)
        return _secure(HTMLResponse(express_page.done(
            slip, f"/v1/tap/done/{session_id[:64]}/status", nonce,
            pay_with=svc.gateway_label(s["tenant_id"]) if s.get("tenant_id") else "PayFast")), nonce)
    return _page("Thank you", "Your slip will arrive in WhatsApp as soon as the payment is confirmed. "
                              "You can close this page.")


@router.get("/v1/tap/done/{session_id}/status")
async def done_status(session_id: str, request: Request) -> Response:
    """The done page polls this until PayFast confirms, then opens the printed slip."""
    import json
    from vula.tap.express import COOKIE
    body = get_service().express.status(session_id[:64], request.cookies.get(COOKIE) or "")
    return Response(json.dumps(body), media_type="application/json", headers={"Cache-Control": "no-store"})


@router.get("/v1/tap/cancelled/{session_id}")
async def cancelled(session_id: str, request: Request):
    from vula.tap.express import COOKIE
    # an express payer who backs out frees the bill at once, so the next tap works straight away
    get_service().express.cancel(session_id[:64], request.cookies.get(COOKIE) or "")
    return _page("Payment cancelled", "You haven't been charged. Tap the tag again to restart.")


# ── merchant endpoints ───────────────────────────────────────────────────────────────────────
class BillIn(BaseModel):
    staff_id: Optional[str] = None         # who served: the bill goes on this person's tag
    tag_id: Optional[str] = None           # or name a till/table tag directly
    amount_cents: int = Field(gt=0, le=10_000_000)
    description: str = Field(min_length=1, max_length=120)
    customer_phone: Optional[str] = None


def _audit(tenant: str, identity: dict, action: str, bill_id: Optional[str] = None, **detail) -> None:
    """Owner actions go in the append-only audit trail (vula/tap/audit.py, migration 232)."""
    from vula.tap import audit
    audit.record(get_service(), tenant, actor_kind="owner", actor=audit.owner_actor(identity),
                 action=action, bill_id=bill_id, **detail)


def _bill_detail(tenant: str, bill_id: str) -> dict:
    b = get_service().repo.get_bill(tenant, bill_id) or {}
    return {"amount_cents": b.get("subtotal_cents"), "description": b.get("description")}


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
    _audit(tenant, identity, "bill.create", bill["id"], amount_cents=body.amount_cents,
           description=body.description, to_customer=bool(body.customer_phone))
    # bill_code is returned ONCE so the merchant can hand it to a customer paying from another number
    return {"id": bill["id"], "status": bill["status"], "bill_code": bill.get("bill_code")}


@router.post("/v1/tap/{tenant}/bills/{bill_id}/cancel")
async def cancel_bill(tenant: str, bill_id: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    detail = _bill_detail(tenant, bill_id)
    if not _guard(tenant).cancel_bill(tenant, bill_id):
        raise HTTPException(status_code=409, detail="This bill can't be cancelled.")
    _audit(tenant, identity, "bill.cancel", bill_id, **detail)
    return {"status": "cancelled"}


@router.post("/v1/tap/{tenant}/bills/{bill_id}/release")
async def release_bill(tenant: str, bill_id: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    if not _guard(tenant).release_bill(tenant, bill_id):
        raise HTTPException(status_code=409, detail="This bill isn't claimed.")
    _audit(tenant, identity, "bill.release", bill_id)
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
    _audit(tenant, identity, "shares.save", default_bp=body.default_share_bp, staff_bp=body.staff_shares)
    return _setup().status(tenant)


@router.post("/v1/tap/{tenant}/staff/{member_id}/tag")
async def setup_tag(tenant: str, member_id: str, rotate: bool = False,
                    identity: dict = Depends(require_tenant_actor)) -> dict:
    out = _setup_call(_setup().ensure_tag, tenant, member_id, rotate)
    _audit(tenant, identity, "tag.replace" if rotate else "tag.create", member_id=member_id,
           member=get_service().repo.staff_name(tenant, member_id))
    return out


@router.post("/v1/tap/{tenant}/test/{member_id}")
async def setup_test(tenant: str, member_id: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    out = _setup_call(_setup().start_test, tenant, member_id)
    _audit(tenant, identity, "test.start", member_id=member_id)
    return out


@router.post("/v1/tap/{tenant}/go-live")
async def setup_go_live(tenant: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    _setup_call(_setup().go_live, tenant)
    _audit(tenant, identity, "mode.go_live")
    return _setup().status(tenant)


@router.post("/v1/tap/{tenant}/pause")
async def setup_pause(tenant: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    _setup_call(_setup().pause, tenant)
    _audit(tenant, identity, "mode.pause")
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
    _audit(tenant, identity, "device.enrol_code", member_id=member_id,
           member=get_service().repo.staff_name(tenant, member_id))
    return {**r, "app_url": f"{settings.dashboard_url.rstrip('/')}/pay/?t={tenant}"}


@router.get("/v1/tap/{tenant}/devices")
async def list_devices(tenant: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    return {"devices": get_service().repo.list_devices(tenant)}


@router.get("/v1/tap/{tenant}/audit")
async def audit_log(tenant: str, limit: int = 100, identity: dict = Depends(require_tenant_actor)) -> dict:
    """The owner's activity list: who created, cancelled, released or wrote off what (append-only)."""
    from vula.tap import audit
    return {"activity": audit.view(get_service(), tenant, limit)}


@router.post("/v1/tap/{tenant}/devices/{device_id}/revoke")
async def revoke_device(tenant: str, device_id: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    _auth().revoke_device(tenant, device_id)
    _audit(tenant, identity, "device.revoke", device_id=device_id)
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
    _audit(tenant, identity, "receipt.revoke", payment_id=payment_id)
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
    _audit(tenant, identity, "reminders.set", max=body.max)
    return {"reminders_max": body.max}


@router.post("/v1/tap/{tenant}/bills/{bill_id}/remind")
async def remind_now(tenant: str, bill_id: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    try:
        await _guard(tenant).send_reminder_now(tenant, bill_id)
    except ReminderError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _audit(tenant, identity, "bill.remind", bill_id)
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
    _audit(tenant, identity, f"bill.close.{body.action}", bill_id, reason=body.reason)
    return {"closed": body.action}


# ── bar tabs (vula/tap/tabs.py, migration 231) ───────────────────────────────────────────────
class TabsSettingsIn(BaseModel):
    enabled: bool
    idle_hours: int = Field(default=3, ge=1, le=24)


class TabItemIn(BaseModel):
    description: str = Field(min_length=1, max_length=80)
    quantity: int = Field(default=1, ge=1, le=99)
    unit_cents: int = Field(ge=0, le=1_000_000)


@router.put("/v1/tap/{tenant}/tabs/settings")
async def set_tabs(tenant: str, body: TabsSettingsIn, identity: dict = Depends(require_tenant_actor)) -> dict:
    get_service().repo.upsert_settings(tenant, {"tabs_enabled": body.enabled, "tab_idle_hours": body.idle_hours})
    _audit(tenant, identity, "tabs.settings", enabled=body.enabled, idle_hours=body.idle_hours)
    return {"enabled": body.enabled, "idle_hours": body.idle_hours}


@router.get("/v1/tap/{tenant}/tabs")
async def list_tabs(tenant: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    """Open tabs with the customer's name and full number (the business sees who it is running a
    tab for; the customer was told when the tab opened)."""
    svc = get_service()
    return {"enabled": svc.tabs.enabled(tenant), "idle_hours": svc.tabs.idle_hours(tenant),
            "tabs": svc.tabs.list_open(tenant, reveal=True)}


def _owner(identity: dict) -> str:
    from vula.tap.audit import owner_actor
    return owner_actor(identity)


def _tab_call(fn, *args, **kw):
    from vula.tap.tabs import TabError
    try:
        return fn(*args, **kw)
    except TabError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/v1/tap/{tenant}/tabs/{bill_id}/items")
async def add_tab_item(tenant: str, bill_id: str, body: TabItemIn,
                       identity: dict = Depends(require_tenant_actor)) -> dict:
    svc = _guard(tenant)
    return _tab_call(svc.tabs.add_item, tenant, bill_id, description=body.description, quantity=body.quantity,
                     unit_cents=body.unit_cents, by=_owner(identity), actor_kind="owner")


@router.post("/v1/tap/{tenant}/tabs/{bill_id}/items/{item_id}/void")
async def void_tab_item(tenant: str, bill_id: str, item_id: str,
                        identity: dict = Depends(require_tenant_actor)) -> dict:
    return _tab_call(_guard(tenant).tabs.void_item, tenant, bill_id, item_id, by=_owner(identity), actor_kind="owner")


@router.post("/v1/tap/{tenant}/tabs/{bill_id}/close")
async def close_empty_tab(tenant: str, bill_id: str, identity: dict = Depends(require_tenant_actor)) -> dict:
    if not _tab_call(_guard(tenant).tabs.close_empty, tenant, bill_id, by=_owner(identity)):
        raise HTTPException(status_code=409, detail="This tab can't be closed.")
    return {"status": "cancelled"}
