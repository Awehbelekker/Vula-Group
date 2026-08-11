"""
vula/dynamics365/credentials.py — per-tenant Dynamics 365 (Dataverse) creds with auto-refresh.

Same shape as vula/microsoft/credentials.py, but Dataverse access tokens are scoped to a
specific org's URL (unlike Graph, which is one universal resource), so org_url is stored
alongside the tokens and used to build the resource-specific scope on every refresh.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx

from config import settings

logger = logging.getLogger(__name__)

_CACHE: dict[str, dict] = {}


def _token_url() -> str:
    return f"{settings.microsoft_authority}/oauth2/v2.0/token"


def _client():
    from supabase import create_client
    return create_client(settings.supabase_url,
                         settings.supabase_service_role_key or settings.supabase_service_key)


def invalidate(tenant_id: str) -> None:
    _CACHE.pop(tenant_id, None)


def store_connection(tenant_id: str, *, org_url: str, access_token: str,
                     refresh_token: str | None, expires_in: int, email: str,
                     scopes: str, connected_by: str = "") -> None:
    from vula.email_imap.credentials import encrypt_secret
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in or 3600))).isoformat()
    row = {
        "tenant_id": tenant_id, "org_url": org_url.rstrip("/"), "email": email,
        "access_token": encrypt_secret(access_token),
        "token_expiry": expiry, "scopes": scopes, "status": "connected",
        "connected_by": connected_by, "connected_at": "now()", "updated_at": "now()",
    }
    if refresh_token:
        row["refresh_token"] = encrypt_secret(refresh_token)
    _client().table("vula_dynamics365_accounts").upsert(row, on_conflict="tenant_id").execute()
    invalidate(tenant_id)


async def _refresh(tenant_id: str, org_url: str, refresh_token: str) -> str | None:
    from vula.email_imap.credentials import encrypt_secret
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(_token_url(), data={
            "client_id": settings.microsoft_client_id,
            "client_secret": settings.microsoft_client_secret,
            "refresh_token": refresh_token, "grant_type": "refresh_token",
            "scope": f"offline_access {org_url.rstrip('/')}/.default"})
        if r.status_code != 200:
            logger.warning("Dynamics365 token refresh failed for %s: %s", tenant_id, r.text[:200])
            return None
        d = r.json()
    access = d.get("access_token")
    expiry = (datetime.now(timezone.utc) + timedelta(seconds=int(d.get("expires_in", 3600)))).isoformat()
    update = {"access_token": encrypt_secret(access), "token_expiry": expiry, "updated_at": "now()"}
    if d.get("refresh_token"):  # MS rotates refresh tokens
        update["refresh_token"] = encrypt_secret(d["refresh_token"])
    try:
        _client().table("vula_dynamics365_accounts").update(update).eq("tenant_id", tenant_id).execute()
    except Exception:
        pass
    invalidate(tenant_id)
    return access


async def get_access_token(tenant_id: str) -> dict | None:
    """Return {access_token, org_url, email} for a connected tenant, refreshing if expired."""
    from vula.email_imap.credentials import decrypt_secret
    try:
        rows = (_client().table("vula_dynamics365_accounts")
                .select("org_url,access_token,refresh_token,token_expiry,email,status")
                .eq("tenant_id", tenant_id).eq("status", "connected").limit(1).execute().data or [])
    except Exception as exc:
        logger.debug("Dynamics365 creds lookup failed for %s: %s", tenant_id, exc)
        return None
    if not rows:
        return None
    r = rows[0]
    org_url = r["org_url"]
    # decrypt_secret() passes plaintext legacy values through unchanged, so this is safe to
    # deploy before any backfill of existing rows has run.
    token = decrypt_secret(r.get("access_token") or "") or None
    email, expiry = r.get("email"), r.get("token_expiry")
    refresh_token = decrypt_secret(r.get("refresh_token") or "") or None
    needs = not token
    if expiry and not needs:
        try:
            needs = datetime.fromisoformat(expiry.replace("Z", "+00:00")) <= datetime.now(timezone.utc) + timedelta(seconds=60)
        except Exception:
            needs = True
    if needs and refresh_token:
        token = await _refresh(tenant_id, org_url, refresh_token)
    return {"access_token": token, "org_url": org_url, "email": email} if token else None
