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


async def check_body_tenant(tenant_id: str, authorization: str) -> None:
    """For endpoints that take the tenant in the request BODY (WhatsApp/Yoco/email connect),
    which the path-based tenant_admin_guard can't see. Same rule and same switch
    (ENFORCE_TENANT_AUTH): otherwise anyone could attach their own WhatsApp number, Yoco keys
    or mailbox to another business's workspace."""
    from config import settings
    if not settings.enforce_tenant_auth:
        return
    if not await is_tenant_member(authorization, tenant_id):
        if not (authorization or "").removeprefix("Bearer ").strip():
            raise HTTPException(status_code=401, detail="Sign in required.")
        raise HTTPException(status_code=403, detail="You don't have access to this workspace.")


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


# ── Member scope: WHAT a signed-in member may see, from their verified login (2026-10-17) ─────
# Until now the dashboard's section gating (navConfig.merchantVisible) was browser-only and fed
# by GET /v1/team/{t}/me?email=… — which trusted whatever email the caller typed and handed an
# unknown one full owner access. A sales rep could read Money just by calling the endpoints.
# This resolves the same rule the dashboard uses, server-side, from the JWT's own email:
#   master, or this tenant's owner login            → full
#   a vula_team_members row matched by email        → full if role owner/manager or no access
#                                                      list (same as /me); inactive → nothing
#   a staff login with no directory row             → full (unchanged behaviour — logins made
#                                                      before access lists existed keep working)
_SCOPE_CACHE: dict[tuple[str, str], tuple[float, dict]] = {}
_SCOPE_TTL = 60.0
_MAX_SCOPE_CACHE = 500

_FULL_TEAM_ROLES = ("owner", "manager")


def scope_from_rows(tenant_id: str, email: str, login_rows: list[dict],
                    team_rows: list[dict]) -> dict:
    """Pure rule (tested directly): the member's {full, access, role, team row fields}."""
    if any(r.get("role") == "master" for r in login_rows):
        return {"full": True, "access": [], "role": "master", "member": None}
    login_role = next((r.get("role") for r in login_rows if r.get("tenant_id") == tenant_id), None)
    e = (email or "").strip().lower()
    member = next((m for m in team_rows if e and (m.get("email") or "").strip().lower() == e), None)
    if login_role == "owner":
        return {"full": True, "access": [], "role": "owner", "member": member}
    if member is None:
        return {"full": True, "access": [], "role": login_role or "staff", "member": None}
    access = list(member.get("access") or [])
    if member.get("active") is False:
        return {"full": False, "access": [], "role": member.get("role"), "member": member}
    full = member.get("role") in _FULL_TEAM_ROLES or not access
    return {"full": full, "access": access, "role": member.get("role"), "member": member}


async def member_scope(authorization: str, tenant_id: str) -> dict | None:
    """The caller's scope on tenant_id, or None if they aren't a member (or not signed in)."""
    if not await is_tenant_member(authorization, tenant_id):
        return None
    token = (authorization or "").removeprefix("Bearer ").strip()
    now = time.monotonic()
    hit = _SCOPE_CACHE.get((token, tenant_id))
    if hit and hit[0] > now:
        return hit[1]

    from vula.api.master_auth import _verify_jwt
    user = await _verify_jwt(token) or {}
    try:
        from vula.commerce import service as cs
        db = cs._client()
        login_rows = (db.table("vula_tenant_users").select("tenant_id,role")
                      .eq("user_id", user.get("id")).limit(10).execute().data or [])
        team_rows = (db.table("vula_team_members").select("id,email,role,access,notify,name,whatsapp,active")
                     .eq("tenant_id", tenant_id).execute().data or [])
    except Exception as exc:
        # Fail closed and don't cache: a DB blip must never widen access, and the next request
        # retries the lookup.
        log.warning("member scope lookup failed: %s", exc)
        return {"full": False, "access": [], "role": None, "member": None, "email": user.get("email")}
    scope = scope_from_rows(tenant_id, user.get("email") or "", login_rows, team_rows)
    scope["email"] = user.get("email")
    if len(_SCOPE_CACHE) > _MAX_SCOPE_CACHE:
        _SCOPE_CACHE.clear()
    _SCOPE_CACHE[(token, tenant_id)] = (now + _SCOPE_TTL, scope)
    return scope


def scope_allows(scope: dict | None, any_of: frozenset) -> bool:
    """A full member passes everything; a restricted one needs at least one of these tab ids.
    An empty any_of means "full members only" (Team, logins)."""
    if not scope:
        return False
    if scope.get("full"):
        return True
    return bool(any_of) and bool(set(scope.get("access") or []) & any_of)


async def require_tenant_admin(tenant: str, authorization: str = Header(default="")) -> dict:
    """require_tenant_actor + full access — for changing the team, roles and logins. Any member
    could PATCH another member's role/access before (only membership was checked), so a
    restricted rep could grant themselves Money."""
    identity = await require_tenant_actor(tenant, authorization)
    if not scope_allows(await member_scope(authorization, tenant), frozenset()):
        raise HTTPException(status_code=403,
                            detail="Only the owner or a manager can change the team and logins.")
    return identity
