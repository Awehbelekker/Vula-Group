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


class ConnectResponse(BaseModel):
    tenant_id: str
    phone_number: str
    phone_number_id: str
    waba_id: str
    verified_name: str
    status: str
    webhook_registered: bool


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
        if settings.whatsapp_register_on_connect and settings.whatsapp_registration_pin:
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
    db.table("vula_whatsapp_accounts").upsert(record, on_conflict="tenant_id").execute()

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
    return rows[0]


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
    """Disconnect WhatsApp for a tenant (marks as disconnected, keeps record)."""
    db = _supabase()
    db.table("vula_whatsapp_accounts").update(
        {"status": "disconnected", "access_token": None}
    ).eq("tenant_id", tenant_id).execute()
    return {"tenant_id": tenant_id, "status": "disconnected"}
