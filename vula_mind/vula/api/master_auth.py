"""
vula/api/master_auth.py — server-side master authentication.

The first REAL authorization boundary in this API: verifies the caller's Supabase Auth JWT
against the auth server, then requires a vula_tenant_users row with role='master' for that
user. Attach as a dependency: `dependencies=[Depends(require_master)]`.

Until now "master" was only a client-side label (a role string the dashboard reads at login) —
every admin endpoint trusted whatever tenant_id appeared in the URL. All new /v1/master/*
endpoints are born behind this check; the most sensitive existing endpoints (tenant
create/update, cross-tenant AI spend) are wrapped with it too. Tenant-scoped enforcement for
merchant endpoints is a separate follow-up initiative.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx
from fastapi import Header, HTTPException

from config import settings

log = logging.getLogger(__name__)

# Verified-token cache so each dashboard page load doesn't hammer the auth server.
_CACHE: dict[str, tuple[float, dict]] = {}  # token -> (monotonic expiry, identity)
_CACHE_TTL = 60.0
_MAX_CACHE = 200


async def _verify_jwt(token: str) -> Optional[dict]:
    """Ask Supabase Auth who this JWT belongs to. None if invalid/expired/unreachable."""
    key = settings.supabase_service_role_key or settings.supabase_service_key
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                f"{settings.supabase_url.rstrip('/')}/auth/v1/user",
                headers={"apikey": key, "Authorization": f"Bearer {token}"},
            )
        if resp.status_code != 200:
            return None
        return resp.json()
    except Exception as exc:
        log.warning("JWT verification call failed: %s", exc)
        return None


def _role_for_user(user_id: str) -> Optional[str]:
    """The caller's platform role from vula_tenant_users (source of truth for 'master')."""
    try:
        from vula.commerce import service as cs
        rows = (cs._client().table("vula_tenant_users").select("role")
                .eq("user_id", user_id).limit(5).execute().data or [])
        roles = {r.get("role") for r in rows}
        if "master" in roles:
            return "master"
        return rows[0].get("role") if rows else None
    except Exception as exc:
        log.warning("role lookup failed for %s: %s", user_id, exc)
        return None


async def require_master(authorization: str = Header(default="")) -> dict:
    """FastAPI dependency: 401 without a valid Supabase session JWT, 403 unless the signed-in
    user's role is 'master'. Returns {user_id, email, role} for audit logging."""
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="Sign in required (missing bearer token).")

    now = time.monotonic()
    cached = _CACHE.get(token)
    if cached and cached[0] > now:
        return cached[1]

    user = await _verify_jwt(token)
    if not user or not user.get("id"):
        raise HTTPException(status_code=401, detail="Invalid or expired session — sign in again.")
    role = _role_for_user(user["id"])
    if role != "master":
        raise HTTPException(status_code=403, detail="Master access required.")

    identity = {"user_id": user["id"], "email": user.get("email"), "role": role}
    if len(_CACHE) > _MAX_CACHE:
        _CACHE.clear()
    _CACHE[token] = (now + _CACHE_TTL, identity)
    return identity
