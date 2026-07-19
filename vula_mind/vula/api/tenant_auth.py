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

log = logging.getLogger(__name__)

_CACHE: dict[tuple[str, str], tuple[float, bool]] = {}  # (token, tenant) -> (expiry, allowed)
_CACHE_TTL = 60.0
_MAX_CACHE = 500


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
            allowed = any(r.get("role") == "master" or r.get("tenant_id") == tenant_id
                          for r in rows)
        except Exception as exc:
            log.warning("tenant membership lookup failed: %s", exc)

    if len(_CACHE) > _MAX_CACHE:
        _CACHE.clear()
    _CACHE[(token, tenant_id)] = (now + _CACHE_TTL, allowed)
    return allowed
