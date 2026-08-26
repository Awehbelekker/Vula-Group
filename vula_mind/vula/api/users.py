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
"""
from __future__ import annotations

import logging
import secrets
from typing import Optional

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from config import settings
from vula.api.tenant_auth import require_tenant_actor
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
_TENANT_USER_ROLES = {"owner", "staff", "master"}


def _map_tenant_user(user_id: str, tenant: str, role: str) -> None:
    login_role = role if role in _TENANT_USER_ROLES else "staff"
    db = _client()
    existing = (db.table("vula_tenant_users").select("user_id")
                .eq("user_id", user_id).eq("tenant_id", tenant).limit(1).execute().data or [])
    if existing:
        db.table("vula_tenant_users").update({"role": login_role}).eq("user_id", user_id).eq("tenant_id", tenant).execute()
    else:
        db.table("vula_tenant_users").insert({"user_id": user_id, "tenant_id": tenant, "role": login_role}).execute()


class CreateUserIn(BaseModel):
    email: str
    name: Optional[str] = None
    role: str = "staff"          # owner | manager | staff
    whatsapp: Optional[str] = None
    access: list = []


@router.post("/{tenant}/users")
async def create_user(tenant: str, body: CreateUserIn,
                      identity: dict = Depends(require_tenant_actor)) -> dict:
    """Create (or attach) a login for this tenant and return a one-time temp password."""
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
        await _auth_admin("PUT", f"/users/{user_id}", {"password": temp, "email_confirm": True})
        created = False

    try:
        _map_tenant_user(user_id, tenant, body.role)
    except Exception as exc:
        log.warning("tenant_users map failed: %s", exc)

    # Directory record (best-effort). Previously only ran (and only matched/stored) by whatsapp —
    # but /v1/team/{tenant}/me (the actual access-gating lookup the dashboard uses) matches by
    # EMAIL, which was never being written here at all, so a login's chosen `access` list could
    # never actually be found again after creation. Now matches by whatsapp OR email (whichever
    # is available) and always stores email, so the restriction this endpoint is asked to set up
    # is actually reachable afterward.
    if body.whatsapp or body.email:
        try:
            db = _client()
            q = db.table("vula_team_members").select("id").eq("tenant_id", tenant)
            q = q.eq("whatsapp", body.whatsapp) if body.whatsapp else q.eq("email", body.email)
            ex = (q.limit(1).execute().data or [])
            row = {"tenant_id": tenant, "name": body.name or body.email, "whatsapp": body.whatsapp,
                   "email": body.email, "role": body.role, "access": body.access or [], "active": True}
            if ex:
                db.table("vula_team_members").update(row).eq("id", ex[0]["id"]).execute()
            else:
                db.table("vula_team_members").insert(row).execute()
        except Exception as exc:
            log.debug("team_members upsert skipped: %s", exc)

    merchant_audit.audit(tenant, identity, "login_created", email=body.email, role=body.role)
    return {"email": body.email, "temp_password": temp, "user_id": user_id, "created": created}


@router.post("/{tenant}/users/{user_id}/reset")
async def reset_password(tenant: str, user_id: str,
                         identity: dict = Depends(require_tenant_actor)) -> dict:
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
                      identity: dict = Depends(require_tenant_actor)) -> dict:
    try:
        _client().table("vula_tenant_users").update({"role": body.role}) \
            .eq("user_id", user_id).eq("tenant_id", tenant).execute()
    except Exception as exc:
        return {"error": str(exc)}
    merchant_audit.audit(tenant, identity, "login_role_changed", user_id=user_id, role=body.role)
    return {"user_id": user_id, "role": body.role}


@router.delete("/{tenant}/users/{user_id}")
async def remove_access(tenant: str, user_id: str,
                        identity: dict = Depends(require_tenant_actor)) -> dict:
    _client().table("vula_tenant_users").delete() \
        .eq("user_id", user_id).eq("tenant_id", tenant).execute()
    merchant_audit.audit(tenant, identity, "login_access_removed", user_id=user_id)
    return {"removed": user_id}


@router.get("/{tenant}/users")
async def list_users(tenant: str) -> dict:
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
