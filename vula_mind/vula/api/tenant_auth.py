"""
vula/api/tenant_auth.py — tenant-scoped authorization (the follow-up to master_auth.py).

Answers one question per request: is the caller's verified Supabase JWT allowed to act on
THIS tenant_id? Allowed = the user has a vula_tenant_users row for that tenant (owner /
manager / staff), or is a master. Used by the tenant_admin_guard middleware in server.py,
which is gated behind settings.enforce_tenant_auth for a dark rollout.

Caching mirrors master_auth: a verified token+tenant pair is cached ~60s so dashboard
navigation doesn't hammer the auth server.
"""
from __future__ import annotations

import logging
import time

from fastapi import Header, HTTPException

log = logging.getLogger(__name__)

_CACHE: dict[tuple[str, str], tuple[float, bool]] = {}  # (token, tenant) -> (expiry, allowed)
_CACHE_TTL = 60.0
_MAX_CACHE = 500

_IDENTITY_CACHE: dict[str, tuple[float, dict]] = {}  # token -> (expiry, {user_id, email})
_IDENTITY_TTL = 60.0
_MAX_IDENTITY_CACHE = 500


async def is_tenant_member(authorization: str, tenant_id: str) -> bool:
    """True if the bearer token belongs to a member of tenant_id, or a master."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token or not tenant_id:
        return False

    now = time.monotonic()
    hit = _CACHE.get((token, tenant_id))
    if hit and hit[0] > now:
        return hit[1]

    from vula.api.master_auth import _verify_jwt
    user = await _verify_jwt(token)
    allowed = False
    if user and user.get("id"):
        try:
            from vula.commerce import service as cs
            rows = (cs._client().table("vula_tenant_users").select("tenant_id,role")
                    .eq("user_id", user["id"]).limit(10).execute().data or [])
            is_master = any(r.get("role") == "master" for r in rows)
            is_member = any(r.get("tenant_id") == tenant_id for r in rows)
            # A suspended tenant's own team loses dashboard access too — master keeps it
            # (needed to reactivate / investigate a suspended tenant).
            if is_master:
                allowed = True
            elif is_member:
                from vula.api import tenants as _tenants
                allowed = _tenants.is_active(tenant_id)
        except Exception as exc:
            log.warning("tenant membership lookup failed: %s", exc)

    if len(_CACHE) > _MAX_CACHE:
        _CACHE.clear()
    _CACHE[(token, tenant_id)] = (now + _CACHE_TTL, allowed)
    return allowed


async def require_tenant_actor(tenant: str, authorization: str = Header(default="")) -> dict:
    """FastAPI dependency for merchant-admin WRITE endpoints (team.py/users.py) that need to
    know WHO acted, for the merchant audit trail (vula/api/merchant_audit.py). Enforces the
    same rule as the tenant_admin_guard middleware this request already passed — belt and
    braces, cheap on a cache hit — and additionally returns {user_id, email} so the write
    endpoint can attribute the audit row to a real signed-in person instead of trusting a
    frontend-supplied field."""
    if not await is_tenant_member(authorization, tenant):
        token = (authorization or "").removeprefix("Bearer ").strip()
        if not token:
            raise HTTPException(status_code=401, detail="Sign in required.")
        raise HTTPException(status_code=403, detail="You don't have access to this workspace.")

    token = authorization.removeprefix("Bearer ").strip()
    now = time.monotonic()
    hit = _IDENTITY_CACHE.get(token)
    if hit and hit[0] > now:
        return hit[1]

    from vula.api.master_auth import _verify_jwt
    user = await _verify_jwt(token) or {}
    identity = {"user_id": user.get("id"), "email": user.get("email")}
    if len(_IDENTITY_CACHE) > _MAX_IDENTITY_CACHE:
        _IDENTITY_CACHE.clear()
    _IDENTITY_CACHE[token] = (now + _IDENTITY_TTL, identity)
    return identity
