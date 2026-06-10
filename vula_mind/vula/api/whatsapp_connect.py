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
from fastapi import APIRouter, HTTPException
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
async def connect_whatsapp(body: ConnectRequest):
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

        # Step 2 — discover WABA and phone numbers for this token
        waba_resp = await client.get(
            f"{GRAPH_BASE}/me/businesses",
            headers=auth_headers,
        )
        businesses = waba_resp.json().get("data", [])

        waba_id = None
        phone_number_id = None
        phone_number = None
        verified_name = None

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
    db = _supabase()
    record = {
        "tenant_id": body.tenant_id,
        "waba_id": waba_id,
        "phone_number_id": phone_number_id,
        "phone_number": phone_number,
        "access_token": access_token,
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
