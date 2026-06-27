"""
vula/google/credentials.py — per-tenant Google creds with auto-refresh.

Google access tokens are short-lived (~1h); we store the refresh token and mint a
fresh access token on demand. One OAuth app (settings.google_client_id/secret)
serves all tenants.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from config import settings

logger = logging.getLogger(__name__)

_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CACHE: dict[str, dict] = {}


def _client():
    from supabase import create_client
    return create_client(settings.supabase_url,
                         settings.supabase_service_role_key or settings.supabase_service_key)


def invalidate(tenant_id: str) -> None:
    _CACHE.pop(tenant_id, None)


def store_connection(tenant_id: str, *, access_token: str, refresh_token: str | None,
                     expires_in: int, email: str, scopes: str, connected_by: str = "") -> None:
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in or 3600))).isoformat()
    row = {
        "tenant_id": tenant_id, "email": email, "access_token": access_token,
        "token_expiry": expiry, "scopes": scopes, "status": "connected",
        "connected_by": connected_by, "connected_at": "now()", "updated_at": "now()",
    }
    if refresh_token:  # Google only returns it on first consent — keep the existing one otherwise
        row["refresh_token"] = refresh_token
    _client().table("vula_google_accounts").upsert(row, on_conflict="tenant_id").execute()
    invalidate(tenant_id)


async def _refresh(tenant_id: str, refresh_token: str) -> str | None:
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(_TOKEN_URL, data={
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token"})
        if r.status_code != 200:
            logger.warning("Google token refresh failed for %s: %s", tenant_id, r.text[:200])
            return None
        d = r.json()
    access = d.get("access_token")
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=int(d.get("expires_in", 3600)))).isoformat()
    try:
        _client().table("vula_google_accounts").update(
            {"access_token": access, "token_expiry": expiry, "updated_at": "now()"}
        ).eq("tenant_id", tenant_id).execute()
    except Exception:
        pass
    invalidate(tenant_id)
    return access


async def get_access_token(tenant_id: str) -> dict | None:
    """Return {access_token, email} for a connected tenant, refreshing if expired."""
    try:
        rows = (_client().table("vula_google_accounts")
                .select("access_token,refresh_token,token_expiry,email,status")
                .eq("tenant_id", tenant_id).eq("status", "connected").limit(1).execute().data or [])
    except Exception as exc:
        logger.debug("google creds lookup failed for %s: %s", tenant_id, exc)
        return None
    if not rows:
        return None
    r = rows[0]
    token, email = r.get("access_token"), r.get("email")
    # Refresh if missing or within 60s of expiry.
    expiry = r.get("token_expiry")
    needs = not token
    if expiry and not needs:
        try:
            needs = datetime.fromisoformat(expiry.replace("Z", "+00:00")) <= datetime.now(timezone.utc) + timedelta(seconds=60)
        except Exception:
            needs = True
    if needs and r.get("refresh_token"):
        token = await _refresh(tenant_id, r["refresh_token"])
    return {"access_token": token, "email": email} if token else None
