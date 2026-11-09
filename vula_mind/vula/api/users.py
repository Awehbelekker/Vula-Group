"""
vula/api/users.py — tenant user management (logins + passwords).

Lets a tenant owner create staff logins and reset passwords without Vula-dev help, and backs
the dashboard's "change my password". Uses the Supabase Auth admin API (service key, server-side
only) + maps the user to the tenant in vula_tenant_users (role) and vula_team_members (directory).

    GET    /v1/users/{tenant}/users                      list logins (email + role)
    POST   /v1/users/{tenant}/users                      create a staff login (returns temp password)
    POST   /v1/users/{tenant}/users/{user_id}/reset      reset a user's password (returns temp)
    PATCH  /v1/users/{tenant}/users/{user_id}            change role / deactivate mapping
    DELETE /v1/users/{tenant}/users/{user_id}            remove this tenant's access for the user

Every route needs full access (owner or manager — tenant_auth.require_tenant_admin, 2026-10-17);
before, any member could create logins or change roles, and the list had no check at all.
"""
from __future__ import annotations

import re
import logging
import secrets
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from config import settings
from vula.api.tenant_auth import require_tenant_admin
from vula.api import merchant_audit

log = logging.getLogger(__name__)
router = APIRouter(tags=["users"])


def _svc_key() -> str:
    return settings.supabase_service_role_key or settings.supabase_service_key


async def _auth_admin(method: str, path: str, json: dict | None = None) -> httpx.Response:
    key = _svc_key()
    base = (settings.supabase_url or "").rstrip("/")
    async with httpx.AsyncClient(timeout=15.0) as c:
        return await c.request(
            method, f"{base}/auth/v1/admin{path}",
            headers={"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=json,
        )


def _client():
    from vula.commerce import service as cs
    return cs._client()


async def _find_user_id(email: str) -> Optional[str]:
    r = await _auth_admin("GET", "/users?per_page=300")
    if r.status_code != 200:
        return None
    users = r.json().get("users", [])
    m = [u for u in users if (u.get("email") or "").lower() == email.lower()]
    return m[0]["id"] if m else None


# vula_tenant_users.role is DB-locked to owner|staff|master (migrations/005_storage_auth.sql) —
# any other vula_team_members-style role label (e.g. "sales_rep", "manager", "bookkeeper") must
# map to a valid login role here. Previously this passed the raw label straight through, which
# silently failed the CHECK constraint (caught by the bare except below) — the Supabase Auth
# user still got created, but with no vula_tenant_users row, so on login they landed in "set up
# your own workspace" instead of the tenant they were meant to join. The real restriction for a
# non-owner role lives in vula_team_members.access (see create_user below), not in this value.
_TENANT_USER_ROLES = {"owner", "staff"}
# "master" is allowed by the DB CHECK but is never something a business may hand out: a master row
# makes is_tenant_member / require_master accept that login on every business and every
# /v1/master route (9 Nov 2026 audit S1: an owner could PATCH their own row to "master").


def _memberships(user_id: str) -> list:
    """Every vula_tenant_users row for this login: [{"tenant_id", "role"}, ...]."""
    return (_client().table("vula_tenant_users").select("tenant_id,role")
            .eq("user_id", user_id).execute().data or [])


def _owned_only_by(user_id: str, tenant: str) -> bool:
    """True when this login belongs to this business and to nothing else (no other business,
    no master row): only then may this business's admin set its password or change its role.
    A login shared with another business, or a master's, is that person's own: the owner can
    give it access here, never a new password (9 Nov 2026 audit S2: "create a login" with the
    master's email returned a fresh password for the master). Any doubt is False."""
    try:
        rows = _memberships(user_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("membership lookup failed for %s, refusing: %s", user_id, exc)
        return False
    return bool(rows) and all(r.get("tenant_id") == tenant and r.get("role") != "master" for r in rows)


def _map_tenant_user(user_id: str, tenant: str, role: str) -> None:
    login_role = role if role in _TENANT_USER_ROLES else "staff"
    db = _client()
    existing = (db.table("vula_tenant_users").select("user_id")
                .eq("user_id", user_id).eq("tenant_id", tenant).limit(1).execute().data or [])
    if existing:
        db.table("vula_tenant_users").update({"role": login_role}).eq("user_id", user_id).eq("tenant_id", tenant).execute()
    else:
        # Plan limit (go-live readiness pass, Phase 4.2) — only a genuinely NEW seat is gated;
        # re-inviting or changing an existing member's role (the `existing` branch above) never
        # consumes an additional one. Raises PlanLimitError on breach — create_user (the only
        # caller) catches it and reports the limit instead of creating the tenant_users row.
        from vula.commerce.plan_limits import check_seat_quota
        check_seat_quota(tenant)
        db.table("vula_tenant_users").insert({"user_id": user_id, "tenant_id": tenant, "role": login_role}).execute()


class CreateUserIn(BaseModel):
    email: str
    name: Optional[str] = None
    role: str = "staff"          # owner | manager | staff
    whatsapp: Optional[str] = None
    access: list = []


@router.post("/{tenant}/users")
async def create_user(tenant: str, body: CreateUserIn,
                      identity: dict = Depends(require_tenant_admin)) -> dict:
    """Create (or attach) a login for this tenant and return a one-time temp password."""
    from vula.commerce.plan_limits import PlanLimitError
    temp = secrets.token_urlsafe(9)
    r = await _auth_admin("POST", "/users", {
        "email": body.email, "password": temp, "email_confirm": True,
        "user_metadata": {"full_name": body.name or body.email},
    })
    if r.status_code in (200, 201):
        user_id = r.json().get("id")
        created = True
    else:
        user_id = await _find_user_id(body.email)
        if not user_id:
            return {"error": f"Could not create user: {r.text[:200]}"}
        created = False
        try:
            rows = _memberships(user_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("membership lookup failed for %s: %s", user_id, exc)
            return {"error": "Couldn't check that login just now. Try again."}
        if any(x.get("role") == "master" for x in rows):
            return {"error": "That email is a Vula admin login. Use a different email."}
        if rows and all(x.get("tenant_id") == tenant for x in rows):
            # Already only this business's login: a new temp password is the owner's to give.
            await _auth_admin("PUT", f"/users/{user_id}", {"password": temp, "email_confirm": True})
        else:
            # Someone else's login (another business, or a sign-up with no business yet). Give it
            # access here; their password stays theirs.
            temp = None

    try:
        _map_tenant_user(user_id, tenant, body.role)
    except PlanLimitError as exc:
        return {"error": str(exc)}
    except Exception as exc:
        log.warning("tenant_users map failed: %s", exc)

    # Directory record (best-effort). Previously only ran (and only matched/stored) by whatsapp —
    # but /v1/team/{tenant}/me (the actual access-gating lookup the dashboard uses) matches by
    # EMAIL, which was never being written here at all, so a login's chosen `access` list could
    # never actually be found again after creation. Now matches by whatsapp OR email (whichever
    # is available) and always stores email, so the restriction this endpoint is asked to set up
    # is actually reachable afterward.
    # 5 Nov 2026: the WhatsApp number is stored as digits, the way Team stores it (so the same
    # person isn't two rows), and a number given for someone already on the team by email is
    # added to that row — it's what WhatsApp sign-in and Vula's messages to them go by.
    whatsapp = re.sub(r"\D", "", body.whatsapp or "") or None
    if whatsapp or body.email:
        try:
            db = _client()
            base = lambda: db.table("vula_team_members").select("id").eq("tenant_id", tenant)  # noqa: E731
            ex = (base().eq("whatsapp", whatsapp).limit(1).execute().data or []) if whatsapp else []
            if not ex and body.email:
                ex = base().eq("email", body.email).limit(1).execute().data or []
            row = {"tenant_id": tenant, "name": body.name or body.email, "email": body.email,
                   "role": body.role, "access": body.access or [], "active": True}
            if whatsapp:
                row["whatsapp"] = whatsapp
            if ex:
                db.table("vula_team_members").update(row).eq("id", ex[0]["id"]).execute()
            else:
                db.table("vula_team_members").insert(row).execute()
            from vula import team_index
            team_index.invalidate(tenant)
        except Exception as exc:
            log.debug("team_members upsert skipped: %s", exc)

    merchant_audit.audit(tenant, identity, "login_created", email=body.email, role=body.role)
    out = {"email": body.email, "temp_password": temp, "user_id": user_id, "created": created}
    if temp is None:
        out["note"] = "This person already has a Vula login. They sign in with their own password."
    return out


@router.post("/{tenant}/users/{user_id}/reset")
async def reset_password(tenant: str, user_id: str,
                         identity: dict = Depends(require_tenant_admin)) -> dict:
    # Master's own page (master.master_reset_user_password) passes require_master's identity,
    # the only one with a role; a business's admin gets none.
    if (identity or {}).get("role") != "master" and not _owned_only_by(user_id, tenant):
        raise HTTPException(status_code=403, detail="You can only reset the password of a login that "
                                                    "belongs to this business alone.")
    temp = secrets.token_urlsafe(9)
    r = await _auth_admin("PUT", f"/users/{user_id}", {"password": temp})
    if r.status_code != 200:
        return {"error": r.text[:200]}
    merchant_audit.audit(tenant, identity, "password_reset", user_id=user_id)
    return {"temp_password": temp}


class RolePatch(BaseModel):
    role: str


@router.patch("/{tenant}/users/{user_id}")
async def update_role(tenant: str, user_id: str, body: RolePatch,
                      identity: dict = Depends(require_tenant_admin)) -> dict:
    if body.role == "master":
        raise HTTPException(status_code=403, detail="Master access can't be given from a business.")
    login_role = body.role if body.role in _TENANT_USER_ROLES else "staff"
    try:
        rows = _memberships(user_id)
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}
    if not any(x.get("tenant_id") == tenant for x in rows):
        raise HTTPException(status_code=404, detail="No such login on this business.")
    if any(x.get("role") == "master" for x in rows):
        # Demoting the master's row here would take away master everywhere.
        raise HTTPException(status_code=403, detail="A Vula admin login's role can't be changed here.")
    try:
        _client().table("vula_tenant_users").update({"role": login_role}) \
            .eq("user_id", user_id).eq("tenant_id", tenant).execute()
    except Exception as exc:
        return {"error": str(exc)}
    merchant_audit.audit(tenant, identity, "login_role_changed", user_id=user_id, role=login_role)
    return {"user_id": user_id, "role": login_role}


@router.delete("/{tenant}/users/{user_id}")
async def remove_access(tenant: str, user_id: str,
                        identity: dict = Depends(require_tenant_admin)) -> dict:
    _client().table("vula_tenant_users").delete() \
        .eq("user_id", user_id).eq("tenant_id", tenant).execute()
    merchant_audit.audit(tenant, identity, "login_access_removed", user_id=user_id)
    return {"removed": user_id}


@router.get("/{tenant}/users")
async def list_users(tenant: str, identity: dict = Depends(require_tenant_admin)) -> dict:
    try:
        rows = (_client().table("vula_tenant_users").select("user_id,role")
                .eq("tenant_id", tenant).execute().data or [])
    except Exception as exc:
        return {"users": [], "error": str(exc)}
    r = await _auth_admin("GET", "/users?per_page=300")
    emap = {}
    if r.status_code == 200:
        for u in r.json().get("users", []):
            emap[u["id"]] = {"email": u.get("email"), "last_sign_in": u.get("last_sign_in_at")}
    return {"users": [{"user_id": x["user_id"], "role": x["role"],
                       "email": emap.get(x["user_id"], {}).get("email"),
                       "last_sign_in": emap.get(x["user_id"], {}).get("last_sign_in")} for x in rows]}
