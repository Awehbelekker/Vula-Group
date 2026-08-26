"""
vula/api/team.py — tenant team directory: members, access scopes, WhatsApp notification prefs.

    GET    /v1/team/{tenant}                 list members
    POST   /v1/team/{tenant}                 add a member
    PATCH  /v1/team/{tenant}/{member_id}     update access/notify/role/active
    DELETE /v1/team/{tenant}/{member_id}
    GET    /v1/team/{tenant}/me?email=       the logged-in member's role/access/notify (UI gating)
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from vula.api.tenant_auth import require_tenant_actor
from vula.api import merchant_audit

log = logging.getLogger(__name__)
router = APIRouter(tags=["team"])


def _client():
    from vula.commerce import service as commerce_service
    return commerce_service._client()


def _digits(p: str) -> str:
    return re.sub(r"\D", "", p or "")


class MemberIn(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    role: str = "staff"
    access: list = []
    notify: list = []
    active: bool = True


class MemberPatch(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    whatsapp: Optional[str] = None
    role: Optional[str] = None
    access: Optional[list] = None
    notify: Optional[list] = None
    active: Optional[bool] = None


@router.get("/{tenant}")
async def list_members(tenant: str) -> dict:
    try:
        rows = (_client().table("vula_team_members").select("*")
                .eq("tenant_id", tenant).order("created_at").execute().data or [])
    except Exception as exc:
        log.debug("team list skipped (run migration 029?): %s", exc)
        rows = []
    return {"tenant_id": tenant, "members": rows, "count": len(rows)}


@router.post("/{tenant}")
async def add_member(tenant: str, body: MemberIn,
                     identity: dict = Depends(require_tenant_actor)) -> dict:
    row = body.model_dump()
    row["tenant_id"] = tenant
    if row.get("whatsapp"):
        row["whatsapp"] = _digits(row["whatsapp"])
    try:
        res = _client().table("vula_team_members").upsert(
            row, on_conflict="tenant_id,whatsapp").execute()
        merchant_audit.audit(tenant, identity, "member_added",
                             name=row.get("name"), email=row.get("email"), role=row.get("role"))
        return {"member": (res.data or [row])[0]}
    except Exception as exc:
        return {"error": f"{exc} (run migration 029?)"}


async def _revoke_matching_login(tenant: str, member_id: str, identity: dict) -> bool:
    """Disabling a team-directory member used to leave their dashboard login untouched — two
    independently-tracked systems (vula_team_members vs vula_tenant_users/Supabase Auth), so
    "Disable" looked like it revoked access but didn't; only the separate "Revoke" button in
    Logins & passwords did (2026-07-24 admin-depth audit finding). If this member's email
    matches a real login, pull it too. Best-effort: a lookup miss just means no login existed."""
    try:
        rows = (_client().table("vula_team_members").select("email")
                .eq("id", member_id).eq("tenant_id", tenant).limit(1).execute().data or [])
        email = (rows[0].get("email") or "").strip() if rows else ""
        if not email:
            return False
        from vula.api.users import _find_user_id, remove_access
        user_id = await _find_user_id(email)
        if not user_id:
            return False
        await remove_access(tenant, user_id, identity=identity)
        return True
    except Exception as exc:
        log.warning("login revoke on disable failed for member %s: %s", member_id, exc)
        return False


@router.patch("/{tenant}/{member_id}")
async def update_member(tenant: str, member_id: str, body: MemberPatch,
                        identity: dict = Depends(require_tenant_actor)) -> dict:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if patch.get("whatsapp"):
        patch["whatsapp"] = _digits(patch["whatsapp"])
    patch["updated_at"] = "now()"
    try:
        _client().table("vula_team_members").update(patch).eq("id", member_id).eq("tenant_id", tenant).execute()
    except Exception as exc:
        return {"error": str(exc)}

    login_revoked = False
    if patch.get("active") is False:
        login_revoked = await _revoke_matching_login(tenant, member_id, identity)

    merchant_audit.audit(tenant, identity, "member_updated", member_id=member_id,
                         changed={k: v for k, v in patch.items() if k != "updated_at"},
                         login_revoked=login_revoked)
    return {"id": member_id, **patch, "login_revoked": login_revoked}


@router.delete("/{tenant}/{member_id}")
async def remove_member(tenant: str, member_id: str,
                        identity: dict = Depends(require_tenant_actor)) -> dict:
    try:
        _client().table("vula_team_members").delete().eq("id", member_id).eq("tenant_id", tenant).execute()
        merchant_audit.audit(tenant, identity, "member_removed", member_id=member_id)
    except Exception:
        pass
    return {"id": member_id, "removed": True}


@router.get("/{tenant}/me")
async def me(tenant: str, email: str = "") -> dict:
    """The logged-in member's profile (role/access/notify) — drives dashboard gating.
    Owners/unknown emails get full access (empty access list = see everything)."""
    try:
        rows = (_client().table("vula_team_members").select("role,access,notify,name,whatsapp")
                .eq("tenant_id", tenant).eq("email", email).limit(1).execute().data or [])
    except Exception:
        rows = []
    if not rows:
        return {"role": "owner", "access": [], "notify": list(_EVENTS), "full": True, "whatsapp": None}
    m = rows[0]
    return {"role": m.get("role"), "access": m.get("access") or [],
            "notify": m.get("notify") or [], "name": m.get("name"), "whatsapp": m.get("whatsapp"),
            "full": (m.get("role") in ("owner", "manager")) or not (m.get("access") or [])}


_EVENTS = ("which_project", "followup_digest", "payment_received", "new_invoice", "new_order",
          "low_stock", "help_request", "procurement_done")


@router.get("/{tenant}/audit")
async def audit_log(tenant: str, limit: int = 100,
                    identity: dict = Depends(require_tenant_actor)) -> dict:
    """Merchant-scoped audit trail (migration 107) — who changed team access/roles/logins,
    and when. The tenant-scoped counterpart to master's /v1/master/audit."""
    return {"events": merchant_audit.list_audit(tenant, limit)}
