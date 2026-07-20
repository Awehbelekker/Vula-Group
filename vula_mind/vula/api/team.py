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

from fastapi import APIRouter
from pydantic import BaseModel

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
async def add_member(tenant: str, body: MemberIn) -> dict:
    row = body.model_dump()
    row["tenant_id"] = tenant
    if row.get("whatsapp"):
        row["whatsapp"] = _digits(row["whatsapp"])
    try:
        res = _client().table("vula_team_members").upsert(
            row, on_conflict="tenant_id,whatsapp").execute()
        return {"member": (res.data or [row])[0]}
    except Exception as exc:
        return {"error": f"{exc} (run migration 029?)"}


@router.patch("/{tenant}/{member_id}")
async def update_member(tenant: str, member_id: str, body: MemberPatch) -> dict:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    if patch.get("whatsapp"):
        patch["whatsapp"] = _digits(patch["whatsapp"])
    patch["updated_at"] = "now()"
    try:
        _client().table("vula_team_members").update(patch).eq("id", member_id).eq("tenant_id", tenant).execute()
    except Exception as exc:
        return {"error": str(exc)}
    return {"id": member_id, **patch}


@router.delete("/{tenant}/{member_id}")
async def remove_member(tenant: str, member_id: str) -> dict:
    try:
        _client().table("vula_team_members").delete().eq("id", member_id).eq("tenant_id", tenant).execute()
    except Exception:
        pass
    return {"id": member_id, "removed": True}


@router.get("/{tenant}/me")
async def me(tenant: str, email: str = "") -> dict:
    """The logged-in member's profile (role/access/notify) — drives dashboard gating.
    Owners/unknown emails get full access (empty access list = see everything)."""
    try:
        rows = (_client().table("vula_team_members").select("role,access,notify,name")
                .eq("tenant_id", tenant).eq("email", email).limit(1).execute().data or [])
    except Exception:
        rows = []
    if not rows:
        return {"role": "owner", "access": [], "notify": list(_EVENTS), "full": True}
    m = rows[0]
    return {"role": m.get("role"), "access": m.get("access") or [],
            "notify": m.get("notify") or [], "name": m.get("name"),
            "full": (m.get("role") in ("owner", "manager")) or not (m.get("access") or [])}


_EVENTS = ("which_project", "followup_digest", "payment_received", "new_invoice", "new_order",
          "low_stock", "help_request", "procurement_done")
