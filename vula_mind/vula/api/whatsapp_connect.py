"""
vula/api/whatsapp_connect.py — Meta Embedded Signup OAuth flow.

This is the backend for the "Connect WhatsApp" button in Vula Admin.
Clients click the button → Meta popup → code returned → this endpoint
exchanges code for token → registers Vula webhook → stores credentials.

After this, WhatsApp ordering works automatically for that client.
Ian never needs to touch Meta credentials again.

Endpoints:
    POST /v1/whatsapp/connect           — exchange code for token + register webhook
    GET  /v1/whatsapp/connect/status/{tenant_id}  — connection status
    DELETE /v1/whatsapp/disconnect/{tenant_id}    — disconnect (revoke)
    GET  /v1/whatsapp/accounts          — list all connected tenants (admin)

Setup required (one-time):
    1. Create Vula Facebook App at developers.facebook.com
    2. Add WhatsApp product → request whatsapp_business_management permission
    3. Enable Embedded Signup in the app settings
    4. Set env vars:
       VULA_FB_APP_ID     — your Facebook App ID
       VULA_FB_APP_SECRET — your Facebook App Secret
       VULA_FB_CONFIG_ID  — Embedded Signup configuration ID
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from config import settings

log = logging.getLogger(__name__)
router = APIRouter(tags=["whatsapp-connect"])

GRAPH_BASE = "https://graph.facebook.com/v19.0"


# ── Supabase helper ───────────────────────────────────────────────────────────

def _supabase():
    from supabase import create_client
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key or settings.supabase_service_key,
    )


# ── Models ────────────────────────────────────────────────────────────────────

class ConnectRequest(BaseModel):
    tenant_id: str
    code: str                    # Short-lived code from Meta Embedded Signup
    connected_by: Optional[str] = None  # email of the admin who clicked Connect
    # From Embedded Signup's session-info message (sessionInfoVersion 2): the WhatsApp account
    # and number the owner actually picked. Without these the first number found on ANY of the
    # token's businesses was used — the wrong one for an owner with several.
    waba_id: Optional[str] = None
    phone_number_id: Optional[str] = None
    # The owner linked the WhatsApp Business app number they already use (Meta's coexistence,
    # Embedded Signup's WhatsApp Business app onboarding): they keep the app on their phone and
    # Vula answers on the same number (vula/api/whatsapp_coexist.py).
    coexistence: bool = False


class ConnectResponse(BaseModel):
    tenant_id: str
    phone_number: str
    phone_number_id: str
    waba_id: str
    verified_name: str
    status: str
    webhook_registered: bool
    coexistence: bool = False


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/connect", response_model=ConnectResponse)
async def connect_whatsapp(body: ConnectRequest, authorization: str = Header(default="")):
    from vula.api.tenant_auth import check_body_tenant
    await check_body_tenant(body.tenant_id, authorization)
    """
    Exchange the short-lived code from Meta Embedded Signup for a
    long-lived access token, discover the phone number details,
    and register Vula's webhook with Meta — all automatically.
    """
    app_id = settings.vula_fb_app_id
    app_secret = settings.vula_fb_app_secret

    if not app_id or not app_secret:
        raise HTTPException(
            status_code=503,
            detail="Vula Facebook App not configured. Set VULA_FB_APP_ID and VULA_FB_APP_SECRET.",
        )

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Step 1 — exchange short-lived code for long-lived token
        token_resp = await client.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "client_id": app_id,
                "client_secret": app_secret,
                "code": body.code,
            },
        )
        if not token_resp.is_success:
            log.error("Token exchange failed: %s", token_resp.text)
            raise HTTPException(status_code=400, detail=f"Token exchange failed: {token_resp.text}")

        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="No access token in Meta response")

        auth_headers = {"Authorization": f"Bearer {access_token}"}

        waba_id = None
        phone_number_id = None
        phone_number = None
        verified_name = None

        # Step 2a — the number the owner chose in the signup popup, when the frontend sent it.
        if body.waba_id and body.phone_number_id:
            chosen = await client.get(f"{GRAPH_BASE}/{body.phone_number_id}",
                                      params={"fields": "id,display_phone_number,verified_name"},
                                      headers=auth_headers)
            if chosen.is_success:
                waba_id, phone_number_id = body.waba_id, body.phone_number_id
                phone_number = chosen.json().get("display_phone_number", "")
                verified_name = chosen.json().get("verified_name", "")
            else:
                log.warning("chosen phone %s not readable with this token, discovering instead: %s",
                            body.phone_number_id, chosen.text[:200])

        # Step 2b — otherwise discover WABA and phone numbers for this token
        businesses = []
        if not phone_number_id:
            waba_resp = await client.get(
                f"{GRAPH_BASE}/me/businesses",
                headers=auth_headers,
            )
            businesses = waba_resp.json().get("data", [])

        for biz in businesses:
            wa_resp = await client.get(
                f"{GRAPH_BASE}/{biz['id']}/whatsapp_business_accounts",
                headers=auth_headers,
            )
            for waba in wa_resp.json().get("data", []):
                phones_resp = await client.get(
                    f"{GRAPH_BASE}/{waba['id']}/phone_numbers",
                    params={"fields": "id,display_phone_number,verified_name,status"},
                    headers=auth_headers,
                )
                phones = phones_resp.json().get("data", [])
                if phones:
                    waba_id = waba["id"]
                    phone = phones[0]
                    phone_number_id = phone["id"]
                    phone_number = phone.get("display_phone_number", "")
                    verified_name = phone.get("verified_name", "")
                    break
            if waba_id:
                break

        if not phone_number_id:
            raise HTTPException(
                status_code=400,
                detail="No WhatsApp phone numbers found for this account. "
                       "Please verify the phone number in Meta Business Manager first.",
            )

        # Step 2c — register the number for Cloud API messaging (a number added through Embedded
        # Signup can't send until it is). Behind a flag until tested live: registering sets the
        # number's two-step PIN, so it needs WHATSAPP_REGISTRATION_PIN, and fails harmlessly on
        # a number that's already registered with a different PIN.
        # Never for a number shared with the WhatsApp Business app: it's already registered, and
        # registering it for the Cloud API alone would take it off the owner's phone.
        if (settings.whatsapp_register_on_connect and settings.whatsapp_registration_pin
                and not body.coexistence):
            reg = await client.post(f"{GRAPH_BASE}/{phone_number_id}/register", headers=auth_headers,
                                    json={"messaging_product": "whatsapp",
                                          "pin": settings.whatsapp_registration_pin})
            if reg.is_success:
                log.info("Registered phone %s for Cloud API", phone_number_id)
            else:
                log.warning("Phone registration failed (non-fatal): %s", reg.text[:300])

        # Step 3 — register Vula's webhook with Meta for this WABA
        webhook_registered = False
        webhook_resp = await client.post(
            f"{GRAPH_BASE}/{waba_id}/subscribed_apps",
            headers=auth_headers,
        )
        if webhook_resp.is_success:
            webhook_registered = True
            log.info("Webhook registered for WABA %s", waba_id)
        else:
            log.warning("Webhook registration failed (non-fatal): %s", webhook_resp.text)

    # Step 3b — a shared app number: ask Meta for the app's contacts and the chat history the
    # owner chose to share (Meta only sends them when asked within 24 hours).
    history_sync = None
    if body.coexistence:
        from vula.api.whatsapp_coexist import request_app_sync
        history_sync = await request_app_sync(phone_number_id, access_token)

    # Step 4 — save credentials to Supabase
    from vula.email_imap.credentials import encrypt_secret
    db = _supabase()
    record = {
        "tenant_id": body.tenant_id,
        "waba_id": waba_id,
        "phone_number_id": phone_number_id,
        "phone_number": phone_number,
        "access_token": encrypt_secret(access_token),
        "token_type": "user",
        "verified_name": verified_name,
        "status": "connected",
        "webhook_registered": webhook_registered,
        "connected_by": body.connected_by,
        "connected_at": "now()",
    }
    try:
        db.table("vula_whatsapp_accounts").upsert(
            {**record, "coexistence": body.coexistence, "history_sync": history_sync},
            on_conflict="tenant_id").execute()
    except Exception as exc:       # migration 223 not applied yet: connect anyway
        log.warning("coexistence columns missing (run migration 223): %s", exc)
        db.table("vula_whatsapp_accounts").upsert(record, on_conflict="tenant_id").execute()
    # The new token is used from the next message on, not after a restart.
    from vula.api import whatsapp as wa
    wa._forget_wa_number(body.tenant_id, phone_number_id)
    _HEALTH_CACHE.pop(body.tenant_id, None)

    log.info(
        "WhatsApp connected: tenant=%s phone=%s waba=%s",
        body.tenant_id, phone_number, waba_id
    )

    # Best-effort: make sure this tenant can reach Ian with platform feedback from day one —
    # never blocks the connect flow if it fails (e.g. Meta template review hiccup).
    try:
        from vula.integrations.platform_support import ensure_template
        tmpl_result = await ensure_template(body.tenant_id)
        log.info("platform-feedback template ensured for %s: %s", body.tenant_id, tmpl_result)
    except Exception as exc:
        log.warning("platform-feedback template provisioning skipped for %s: %s", body.tenant_id, exc)

    return ConnectResponse(
        tenant_id=body.tenant_id,
        phone_number=phone_number or "",
        phone_number_id=phone_number_id,
        waba_id=waba_id,
        verified_name=verified_name or "",
        status="connected",
        webhook_registered=webhook_registered,
        coexistence=body.coexistence,
    )


@router.get("/connect/status/{tenant_id}")
async def whatsapp_status(tenant_id: str):
    """Check WhatsApp connection status for a tenant."""
    db = _supabase()
    # .limit(1) (not .maybe_single) — maybe_single 406s on zero rows in this
    # supabase-py version, which would show a fresh tenant as 'error'.
    result = (
        db.table("vula_whatsapp_accounts")
        .select("tenant_id,phone_number,phone_number_id,waba_id,verified_name,status,webhook_registered,connected_at")
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        return {"tenant_id": tenant_id, "status": "not_connected"}
    try:   # whether the number is shared with the WhatsApp Business app (migration 223)
        extra = (db.table("vula_whatsapp_accounts").select("coexistence").eq("tenant_id", tenant_id)
                 .limit(1).execute().data or [])
    except Exception:
        extra = []
    return {**rows[0], "coexistence": bool(extra and extra[0].get("coexistence"))}


@router.get("/accounts")
async def list_whatsapp_accounts():
    """List all connected WhatsApp accounts — Vula Admin overview."""
    db = _supabase()
    result = (
        db.table("vula_whatsapp_accounts")
        .select("tenant_id,company_name,phone_number,status,webhook_registered,connected_at,verified_name")
        .order("connected_at", desc=True)
        .execute()
    )
    return {"accounts": result.data or [], "count": len(result.data or [])}


@router.delete("/disconnect/{tenant_id}")
async def disconnect_whatsapp(tenant_id: str):
    """Disconnect WhatsApp for a tenant (marks as disconnected, keeps record). Messages stop at
    once on this worker; within _WA_CREDS_TTL on the other (whatsapp._get_tenant_wa_creds)."""
    db = _supabase()
    rows = (db.table("vula_whatsapp_accounts").select("phone_number_id")
            .eq("tenant_id", tenant_id).limit(1).execute().data or [])
    db.table("vula_whatsapp_accounts").update(
        {"status": "disconnected", "access_token": None}
    ).eq("tenant_id", tenant_id).execute()
    from vula.api import whatsapp as wa
    wa._forget_wa_number(tenant_id, (rows[0].get("phone_number_id") if rows else None))
    _HEALTH_CACHE.pop(tenant_id, None)
    return {"tenant_id": tenant_id, "status": "disconnected"}


# ── Number health (2026-10-24, EZContact brief PR 4) ──────────────────────────
# What Meta says about the number: quality, how many new customers a day it may message, whether
# the display name is approved, and whether it's registered to send at all. Read with the
# business's own credentials, cached briefly; the token never leaves the server.

_HEALTH_CACHE: dict = {}
_HEALTH_TTL = 600.0
_HEALTH_FIELDS = ("display_phone_number,verified_name,quality_rating,messaging_limit_tier,"
                  "name_status,status,platform_type,code_verification_status,throughput")
_LIMITS = {"TIER_50": "50", "TIER_250": "250", "TIER_1K": "1,000", "TIER_10K": "10,000",
           "TIER_100K": "100,000", "TIER_UNLIMITED": "unlimited"}


def health_summary(meta: dict) -> dict:
    """Plain-language rows from Meta's phone-number fields. Missing fields are left out."""
    rows = []
    q = (meta.get("quality_rating") or "").upper()
    if q:
        rows.append({"label": "Quality", "value": {"GREEN": "Green — good", "YELLOW": "Yellow — some customers "
                     "blocked or reported recent messages", "RED": "Red — messages may be limited soon"}
                     .get(q, q.title()), "tone": {"GREEN": "ok", "YELLOW": "warn", "RED": "danger"}.get(q, "warn")})
    tier = meta.get("messaging_limit_tier") or ""
    if tier:
        n = _LIMITS.get(tier, tier.replace("TIER_", ""))
        rows.append({"label": "Daily limit", "value": ("No limit on new customers a day" if n == "unlimited"
                     else f"{n} new customers a day"), "tone": "ok"})
    name = (meta.get("name_status") or "").upper()
    if name:
        ok = name in ("APPROVED", "AVAILABLE_WITHOUT_REVIEW")
        rows.append({"label": "Display name", "value": ("Approved" if ok else
                     {"PENDING_REVIEW": "Waiting for Meta's review", "DECLINED": "Declined by Meta — change it in "
                      "WhatsApp Manager", "EXPIRED": "Expired"}.get(name, name.replace("_", " ").title())),
                     "tone": "ok" if ok else "warn"})
    if meta.get("platform_type"):
        reg = meta.get("platform_type") == "CLOUD_API"
        rows.append({"label": "Sending", "value": "Registered to send" if reg else
                     "Not registered to send yet — ask Vula to finish setting up the number",
                     "tone": "ok" if reg else "danger"})
    return {"rows": rows, "number": meta.get("display_phone_number") or "",
            "name": meta.get("verified_name") or "", "status": meta.get("status") or ""}


@router.get("/health/{tenant_id}")
async def whatsapp_health(tenant_id: str):
    import time as _t
    hit = _HEALTH_CACHE.get(tenant_id)
    if hit and _t.time() - hit[0] < _HEALTH_TTL:
        return hit[1]
    from vula.api import whatsapp as wa
    creds = await wa._get_tenant_wa_creds(tenant_id)
    rows = (_supabase().table("vula_whatsapp_accounts").select("status,phone_number_id")
            .eq("tenant_id", tenant_id).limit(1).execute().data or [])
    if not creds or not rows or rows[0].get("status") != "connected":
        return {"tenant_id": tenant_id, "connected": False, "rows": []}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{GRAPH_BASE}/{creds['phone_id']}", params={"fields": _HEALTH_FIELDS},
                                    headers={"Authorization": f"Bearer {creds['token']}"})
        if not resp.is_success:
            log.info("number health read failed for %s: HTTP %s", tenant_id, resp.status_code)
            return {"tenant_id": tenant_id, "connected": True, "rows": [],
                    "error": "Meta didn't answer just now — try again in a minute."}
        out = {"tenant_id": tenant_id, "connected": True, **health_summary(resp.json())}
    except httpx.HTTPError as exc:
        log.info("number health read failed for %s: %s", tenant_id, type(exc).__name__)
        return {"tenant_id": tenant_id, "connected": True, "rows": [],
                "error": "Meta didn't answer just now — try again in a minute."}
    _HEALTH_CACHE[tenant_id] = (_t.time(), out)
    return out


# ── A number added straight to Vula's own WhatsApp Business Account ───────────
# 2026-10-13 (SeedleMe): Embedded Signup stalled, so the number was added in WhatsApp Manager on
# Vula's account instead. Such a number needs no token of its own (the server's system-user
# token covers the whole account — whatsapp._get_tenant_wa_creds sends from the row's own
# phone_number_id); it only needs Vula to know which business it belongs to. Master links it
# here: Meta is asked about the number first, so a typo or another company's number is refused.

class LinkNumberError(ValueError):
    pass


async def link_existing_number(tenant_id: str, phone_number_id: str, pin: Optional[str] = None,
                               linked_by: Optional[str] = None) -> dict:
    """Link a number already on one of Vula's WhatsApp Business Accounts to a tenant. Registers it
    for the Cloud API when Meta says it isn't yet and a two-step PIN is given (the PIN is passed
    to Meta and never stored or logged)."""
    pnid = (phone_number_id or "").strip()
    if not pnid.isdigit():
        raise LinkNumberError("Paste the phone number ID from WhatsApp Manager — digits only.")
    token = settings.whatsapp_token
    if not token:
        raise LinkNumberError("The server has no WhatsApp token configured.")
    db = _supabase()
    rows = db.table("vula_whatsapp_accounts").select("tenant_id,waba_id,phone_number_id").execute().data or []
    other = next((r for r in rows if r.get("phone_number_id") == pnid and r.get("tenant_id") != tenant_id), None)
    if other:
        raise LinkNumberError(f"That number is already linked to '{other['tenant_id']}'.")
    wabas = sorted({r["waba_id"] for r in rows if r.get("waba_id")})
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        found_waba, phone = None, None
        for waba in wabas:
            resp = await client.get(f"{GRAPH_BASE}/{waba}/phone_numbers", headers=headers,
                                    params={"fields": "id,display_phone_number,verified_name,"
                                                      "code_verification_status,name_status,status",
                                            "limit": 100})
            if not resp.is_success:
                continue
            phone = next((p for p in resp.json().get("data", []) if p.get("id") == pnid), None)
            if phone:
                found_waba = waba
                break
        if not phone:
            raise LinkNumberError("That number isn't on Vula's WhatsApp account. Add it in WhatsApp "
                                  "Manager first, then copy its phone number ID.")
        registered = None
        if (phone.get("status") or "").upper() != "CONNECTED" and pin:
            reg = await client.post(f"{GRAPH_BASE}/{pnid}/register", headers=headers,
                                    json={"messaging_product": "whatsapp", "pin": str(pin).strip()})
            registered = reg.is_success
            if not reg.is_success:
                err = ((reg.json() if reg.content else {}).get("error") or {}).get("message") or reg.text[:200]
                raise LinkNumberError(f"Meta refused to register the number: {err}")
    try:
        from vula.api.tenants import _effective_modules, get_config
        mode = "commerce" if "orders" in _effective_modules(get_config(tenant_id, fresh=True) or {}) else "knowledge"
    except Exception:
        mode = "commerce"
    from datetime import datetime, timezone
    record = {"tenant_id": tenant_id, "waba_id": found_waba, "phone_number_id": pnid,
              "phone_number": phone.get("display_phone_number") or "",
              "verified_name": phone.get("verified_name") or "", "access_token": None,
              "token_type": "system", "status": "connected", "route_mode": mode,
              "webhook_registered": True, "connected_by": linked_by,
              "connected_at": datetime.now(timezone.utc).isoformat()}
    db.table("vula_whatsapp_accounts").upsert(record, on_conflict="tenant_id").execute()
    # Forget anything cached about this number or tenant so the next message routes and replies right.
    from vula.api import whatsapp as wa
    wa._forget_wa_number(tenant_id, pnid)
    log.info("WhatsApp number %s linked to %s (waba %s, mode %s)", pnid, tenant_id, found_waba, mode)
    return {"tenant_id": tenant_id, "phone_number_id": pnid, "waba_id": found_waba,
            "phone_number": record["phone_number"], "verified_name": record["verified_name"],
            "meta_status": phone.get("status"), "name_status": phone.get("name_status"),
            "code_verification_status": phone.get("code_verification_status"),
            "registered_now": registered, "route_mode": mode}
