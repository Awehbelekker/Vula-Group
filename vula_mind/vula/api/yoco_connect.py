"""
vula/api/yoco_connect.py — Per-tenant Yoco account management.

Powers the "Connect Yoco" button in Vula Admin. Each tenant pastes
their Yoco API keys (from portal.yoco.com → Developer settings) and
this endpoint:

  1. Validates the keys against the Yoco API
  2. Optionally registers a webhook automatically
  3. Stores credentials in Supabase (encrypted in production)

After connect, the commerce checkout endpoint uses these per-tenant keys
instead of the global YOCO_SECRET_KEY env var.

Endpoints:
    POST   /v1/yoco/connect                — save + validate Yoco keys
    GET    /v1/yoco/status/{tenant_id}     — check connection status
    POST   /v1/yoco/test/{tenant_id}       — re-test keys
    DELETE /v1/yoco/disconnect/{tenant_id} — remove keys
    GET    /v1/yoco/accounts               — list all (admin)
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from config import settings

log = logging.getLogger(__name__)
router = APIRouter(tags=["yoco-connect"])

YOCO_BASE = "https://payments.yoco.com/api"


def _supabase():
    from supabase import create_client
    return create_client(
        settings.supabase_url,
        settings.supabase_service_role_key or settings.supabase_service_key,
    )


# ── Models ────────────────────────────────────────────────────────────────────

class ConnectYocoRequest(BaseModel):
    tenant_id: str
    secret_key: str       # sk_live_... or sk_test_...
    public_key: Optional[str] = None
    webhook_secret: Optional[str] = None
    connected_by: Optional[str] = None
    auto_register_webhook: bool = True

    @field_validator("secret_key")
    @classmethod
    def _check_secret(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("sk_live_") or v.startswith("sk_test_")):
            raise ValueError("secret_key must start with sk_live_ or sk_test_")
        return v


class ConnectYocoResponse(BaseModel):
    tenant_id: str
    mode: str
    status: str
    webhook_registered: bool
    masked_secret: str    # last 4 chars only


def _mask(key: str) -> str:
    if not key or len(key) < 8:
        return "****"
    return f"{key[:8]}…{key[-4:]}"


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/connect", response_model=ConnectYocoResponse)
async def connect_yoco(body: ConnectYocoRequest):
    """
    Save and validate a tenant's Yoco API keys.
    Tests the keys with a no-op Yoco API call, then stores in Supabase.
    """
    mode = "test" if body.secret_key.startswith("sk_test_") else "live"
    webhook_registered = False
    webhook_id = None
    webhook_secret = body.webhook_secret

    # Step 1 — validate keys by calling Yoco
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            # Yoco doesn't have a "ping" endpoint — we try to create a
            # tiny test checkout and immediately discard it.
            # Using checkouts list is the safest read-only check, but
            # Yoco API may differ. We'll attempt a small test checkout.
            test_resp = await client.post(
                f"{YOCO_BASE}/checkouts",
                headers={
                    "Authorization": f"Bearer {body.secret_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "amount": 200,           # R2.00 — Yoco's own minimum; R1.00 was rejected
                                              # with "cannot process a payment for less than
                                              # R2.00", which made this validation fail for
                                              # every valid key (confirmed live 2026-07-25) —
                                              # never charged either way, just a checkout session.
                    "currency": "ZAR",
                    "metadata": {"vula_test": "true", "tenant_id": body.tenant_id},
                },
            )
            if test_resp.status_code in (401, 403):
                raise HTTPException(
                    status_code=400,
                    detail="Yoco rejected the secret key. Check the key and try again.",
                )
            if not test_resp.is_success:
                log.warning("Yoco test failed (%d): %s", test_resp.status_code, test_resp.text)
                raise HTTPException(
                    status_code=400,
                    detail=f"Yoco API error: {test_resp.status_code}",
                )
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Could not reach Yoco: {exc}")

        # Step 2 — optionally register webhook automatically
        if body.auto_register_webhook:
            webhook_url = f"{settings.vula_base_url}/v1/yoco/webhook"
            try:
                wh_resp = await client.post(
                    f"{YOCO_BASE}/webhooks",
                    headers={"Authorization": f"Bearer {body.secret_key}"},
                    json={
                        "name": f"Vula Commerce — {body.tenant_id}",
                        "url": webhook_url,
                    },
                )
                if wh_resp.is_success:
                    wh_data = wh_resp.json()
                    webhook_registered = True
                    webhook_id = wh_data.get("id")
                    webhook_secret = wh_data.get("secret") or webhook_secret
                    log.info("Yoco webhook registered for %s: %s", body.tenant_id, webhook_id)
                else:
                    log.warning("Yoco webhook reg failed (non-fatal): %s", wh_resp.text)
            except Exception as exc:
                log.warning("Yoco webhook reg exception (non-fatal): %s", exc)

    # Step 3 — save to Supabase. Encrypted at rest (Fernet) — same mechanism/key already used
    # for email credentials (vula.email_imap.credentials); decrypt_secret() returns a value
    # unchanged if it wasn't encrypted, so existing plaintext-stored rows keep working until
    # they're next re-saved through this endpoint.
    from vula.email_imap.credentials import encrypt_secret
    db = _supabase()
    record = {
        "tenant_id": body.tenant_id,
        "secret_key": encrypt_secret(body.secret_key),
        "public_key": body.public_key,
        "webhook_secret": encrypt_secret(webhook_secret) if webhook_secret else webhook_secret,
        "mode": mode,
        "status": "connected",
        "webhook_registered": webhook_registered,
        "webhook_id": webhook_id,
        "connected_by": body.connected_by,
        "connected_at": "now()",
        "last_test_at": "now()",
        "last_error": None,
    }
    db.table("vula_yoco_accounts").upsert(record, on_conflict="tenant_id").execute()

    log.info("Yoco connected: tenant=%s mode=%s webhook=%s", body.tenant_id, mode, webhook_registered)

    return ConnectYocoResponse(
        tenant_id=body.tenant_id,
        mode=mode,
        status="connected",
        webhook_registered=webhook_registered,
        masked_secret=_mask(body.secret_key),
    )


@router.get("/status/{tenant_id}")
async def yoco_status(tenant_id: str):
    """Get the Yoco connection status for a tenant (no secret keys returned)."""
    db = _supabase()
    result = (
        db.table("vula_yoco_accounts")
        .select("tenant_id,mode,status,webhook_registered,connected_at,last_test_at,secret_key,public_key")
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        # No per-tenant record — payments may still work via the YOCO_SECRET_KEY
        # env fallback used at checkout. Report that so admin sees the true state.
        env_fallback = bool(getattr(settings, "yoco_secret_key", ""))
        return {
            "tenant_id": tenant_id,
            "status": "env_fallback" if env_fallback else "not_connected",
            "env_fallback": env_fallback,
        }

    from vula.email_imap.credentials import decrypt_secret
    data = rows[0]
    return {
        "tenant_id": tenant_id,
        "mode": data.get("mode"),
        "status": data.get("status"),
        "webhook_registered": data.get("webhook_registered"),
        "connected_at": data.get("connected_at"),
        "last_test_at": data.get("last_test_at"),
        "masked_secret": _mask(decrypt_secret(data.get("secret_key") or "")),
        "public_key": data.get("public_key"),
    }


@router.post("/test/{tenant_id}")
async def yoco_test(tenant_id: str):
    """Re-test the stored Yoco keys against the Yoco API."""
    from vula.email_imap.credentials import decrypt_secret
    db = _supabase()
    result = (
        db.table("vula_yoco_accounts")
        .select("secret_key")
        .eq("tenant_id", tenant_id)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    if not rows:
        raise HTTPException(status_code=404, detail="No Yoco account for this tenant")

    secret = decrypt_secret(rows[0]["secret_key"])
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{YOCO_BASE}/checkouts",
            headers={"Authorization": f"Bearer {secret}"},
            json={"amount": 200, "currency": "ZAR", "metadata": {"vula_test": "true"}},  # R2.00 min
        )

    ok = resp.is_success
    db.table("vula_yoco_accounts").update({
        "status": "connected" if ok else "error",
        "last_test_at": "now()",
        "last_error": None if ok else f"Yoco returned {resp.status_code}",
    }).eq("tenant_id", tenant_id).execute()

    return {"tenant_id": tenant_id, "ok": ok, "status_code": resp.status_code}


@router.delete("/disconnect/{tenant_id}")
async def disconnect_yoco(tenant_id: str):
    """Remove Yoco credentials for a tenant. Existing pending orders will fail."""
    db = _supabase()
    db.table("vula_yoco_accounts").update({
        "status": "disconnected",
        "secret_key": "",
        "webhook_secret": None,
    }).eq("tenant_id", tenant_id).execute()
    return {"tenant_id": tenant_id, "status": "disconnected"}


@router.get("/accounts")
async def list_yoco_accounts():
    """List all connected Yoco accounts — admin overview."""
    db = _supabase()
    result = (
        db.table("vula_yoco_accounts")
        .select("tenant_id,mode,status,webhook_registered,connected_at,last_test_at")
        .order("connected_at", desc=True)
        .execute()
    )
    return {"accounts": result.data or [], "count": len(result.data or [])}
