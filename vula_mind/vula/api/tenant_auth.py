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
import re
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

# A member with no email on their team row who signs in by WhatsApp gets a login under
# wa-<their number>@users.vula-ai.com (vula/api/wa_login.py::_login_for). That address is on no
# team row, so the email match below found nobody and the login fell through to "a staff login
# with no directory row → full": a restricted rep who signed in by WhatsApp saw everything.
_WA_LOGIN_EMAIL = re.compile(r"^wa-(\d{6,15})@users\.vula-ai\.com$")


def _member_for_wa_login(email: str, team_rows: list[dict]) -> dict | None:
    """The team row a WhatsApp-login address belongs to, matched on the number in it."""
    m = _WA_LOGIN_EMAIL.match(email or "")
    if not m:
        return None
    from vula.team_index import digits_za
    target = digits_za(m.group(1))
    return next((r for r in team_rows if target and digits_za(r.get("whatsapp") or "") == target), None)


# ── The accountant's login (19 Nov 2026, Phase 3 of Xero-quality books) ─────────────────────
# An outside accountant the owner invites: sees the books, reports, bank lines, invoices and the
# finance documents, changes nothing, takes no paid seat (plan_limits counts owner/staff only), never
# reaches the WhatsApp admin agent, and every view and download is on record (server.py's guard).
# Keyed on the LOGIN row (role 'accountant', migration 292), so neither a missing team row nor an
# empty access list can ever widen it.
ACCOUNTANT = "accountant"
ACCOUNTANT_TABS = ["books", "bank", "finances", "invoices", "expenses", "documents"]


def _expired(member: dict | None) -> bool:
    from datetime import date
    end = (member or {}).get("expires_on")
    try:
        return bool(end) and date.fromisoformat(str(end)[:10]) < date.today()
    except ValueError:
        return True


def accountant_scope(member: dict | None) -> dict:
    """Read-only, never full; nothing at all without an active, unexpired team row."""
    ok = member is not None and member.get("active") is not False and not _expired(member)
    return {"full": False, "access": list(ACCOUNTANT_TABS) if ok else [], "role": ACCOUNTANT,
            "read_only": True, "member": member}


def scope_from_rows(tenant_id: str, email: str, login_rows: list[dict],
                    team_rows: list[dict]) -> dict:
    """Pure rule (tested directly): the member's {full, access, role, team row fields}."""
    if any(r.get("role") == "master" for r in login_rows):
        return {"full": True, "access": [], "role": "master", "member": None}
    login_role = next((r.get("role") for r in login_rows if r.get("tenant_id") == tenant_id), None)
    e = (email or "").strip().lower()
    member = next((m for m in team_rows if e and (m.get("email") or "").strip().lower() == e), None)
    if login_role == ACCOUNTANT:
        return accountant_scope(member)
    if member is None:
        member = _member_for_wa_login(e, team_rows)
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
        try:
            team_rows = (db.table("vula_team_members")
                         .select("id,email,role,access,notify,name,whatsapp,active,expires_on,can_adjust")
                         .eq("tenant_id", tenant_id).execute().data or [])
        except Exception:  # noqa: BLE001 — before migration 292
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


# ── Which businesses a login is an accountant for (the guard's deny-by-default) ────────────────
_ACC_CACHE: dict[str, tuple[float, dict | None]] = {}


async def accountant_login(authorization: str) -> dict | None:
    """{"user_id", "email", "tenants": {...}, "only": bool} when this login is an accountant for at
    least one business ("only": it is nothing else anywhere), else None. Cached 60 s per token."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    if not token:
        return None
    now = time.monotonic()
    hit = _ACC_CACHE.get(token)
    if hit and hit[0] > now:
        return hit[1]
    from vula.api.master_auth import _verify_jwt
    user = await _verify_jwt(token) or {}
    out = None
    if user.get("id"):
        try:
            from vula.commerce import service as cs
            rows = (cs._client().table("vula_tenant_users").select("tenant_id,role")
                    .eq("user_id", user["id"]).limit(200).execute().data or [])
        except Exception as exc:  # noqa: BLE001
            log.warning("accountant login lookup failed: %s", exc)
            rows = []
        tenants = {r["tenant_id"] for r in rows if r.get("role") == ACCOUNTANT}
        if tenants:
            out = {"user_id": user["id"], "email": user.get("email"), "tenants": tenants,
                   "only": all(r.get("role") == ACCOUNTANT for r in rows)}
    if len(_ACC_CACHE) > _MAX_CACHE:
        _ACC_CACHE.clear()
    _ACC_CACHE[token] = (now + _CACHE_TTL, out)
    return out


async def is_accountant_on(authorization: str, tenant_id: str) -> dict | None:
    acc = await accountant_login(authorization)
    return acc if acc and tenant_id in acc["tenants"] else None
