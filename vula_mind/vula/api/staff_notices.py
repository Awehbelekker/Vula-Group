"""
vula/api/staff_notices.py — a business's notices to its own staff (vula/staff_notices.py).

    GET  /v1/team/{tenant}/notices                  the owner/manager: every notice with who has
                                                    read it; anyone else: the notices sent to them
    GET  /v1/team/{tenant}/notices/settings         birthdays / holidays / send hour
    PUT  /v1/team/{tenant}/notices/settings
    POST /v1/team/{tenant}/notices/preview          who it would reach and the exact wording
    POST /v1/team/{tenant}/notices                  save (send_now / send_at to send it)
    POST /v1/team/{tenant}/notices/{id}/test        to the sender's own WhatsApp only
    POST /v1/team/{tenant}/notices/{id}/send
    POST /v1/team/{tenant}/notices/{id}/cancel
    POST /v1/team/{tenant}/notices/{id}/ack         a staff member's "Got it" on the noticeboard

Writes need the owner or a manager (tenant_auth.require_tenant_admin), except ack, which any
member makes for themselves (server.py's _SECTION_RULES lets that one path through).
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from vula import staff_notices as sn
from vula.api import merchant_audit
from vula.api.tenant_auth import member_scope, require_tenant_admin
from vula.team_index import digits_za

log = logging.getLogger(__name__)
router = APIRouter(tags=["staff-notices"])

_BG_OVER = 20          # bigger teams are sent in the background (10 a second)


class NoticeIn(BaseModel):
    title: str
    body: str
    image_url: Optional[str] = None
    must_read: bool = False
    audience: Optional[dict] = None       # {"all": true} | {"roles": [...]} | {"ids": [...]}
    send_at: Optional[str] = None         # ISO time; omit with send_now for straight away
    send_now: bool = False


class PreviewIn(BaseModel):
    title: str = ""
    body: str = ""
    image_url: Optional[str] = None
    must_read: bool = False
    audience: Optional[dict] = None


class SettingsIn(BaseModel):
    birthdays: Optional[bool] = None
    holidays: Optional[bool] = None
    send_hour: Optional[int] = None
    holiday_note: Optional[str] = None


def _member_of(scope: Optional[dict], tenant: str) -> Optional[dict]:
    m = (scope or {}).get("member") or {}
    phone = digits_za(m.get("whatsapp") or "")
    if not phone:
        return None
    return next((r for r in sn.members(tenant) if digits_za(r.get("whatsapp") or "") == phone), None)


@router.get("/{tenant}/notices")
async def list_notices(tenant: str, authorization: str = Header(default="")) -> dict:
    scope = await member_scope(authorization, tenant)
    try:
        rows = sn.list_notices(tenant)
    except Exception as exc:
        log.debug("notices list skipped (run migration 218?): %s", exc)
        return {"notices": [], "full": bool((scope or {}).get("full"))}
    if scope and scope.get("full"):
        return {"notices": rows, "full": True}
    me = _member_of(scope, tenant)
    mine = []
    for r in rows:
        mine_row = next((x for x in r.get("recipients") or []
                         if me and str(x.get("member_id")) == str(me["id"])), None)
        if mine_row and r.get("status") == "sent":
            mine.append({k: r.get(k) for k in ("id", "kind", "title", "body", "image_url",
                                               "must_read", "sent_at")}
                        | {"acknowledged_at": mine_row.get("acknowledged_at")})
    return {"notices": mine, "full": False}


@router.get("/{tenant}/notices/settings")
async def get_settings(tenant: str) -> dict:
    return {"settings": sn.settings_for(tenant)}


@router.put("/{tenant}/notices/settings")
async def put_settings(tenant: str, body: SettingsIn,
                       identity: dict = Depends(require_tenant_admin)) -> dict:
    patch = {k: v for k, v in body.model_dump().items() if v is not None}
    try:
        saved = sn.save_settings(tenant, patch)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Couldn't save ({exc}). Run migration 218?")
    merchant_audit.audit(tenant, identity, "staff_notice_settings", changed=patch)
    return {"settings": saved}


@router.post("/{tenant}/notices/preview")
async def preview(tenant: str, body: PreviewIn,
                  identity: dict = Depends(require_tenant_admin)) -> dict:
    people = sn.audience(tenant, body.audience)
    sample = {"id": "preview", "kind": "notice", "title": body.title or "Title",
              "body": body.body or "Your message", "must_read": body.must_read,
              "image_url": body.image_url}
    business = sn._business(tenant)
    templates = sum(1 for m in people if not sn._in_window(m) or body.image_url)
    return {"count": len(people), "names": [m.get("name") or m["whatsapp"] for m in people],
            "text": sn.message_text(sample, people[0] if people else {}, business),
            "templates": templates,
            "note": (f"{templates} of {len(people)} haven't messaged in the last day, so they get "
                     "the approved notice template (Meta charges a small fee per message).")
            if templates else "Everyone messaged recently — this goes as a normal message."}


async def _send(tenant: str, notice_id: str, size: int) -> dict:
    if size > _BG_OVER:
        from vula.commerce.background_tasks import run_background
        run_background(tenant, "staff_notice_send", sn.send(tenant, notice_id))
        return {"status": "sending", "total": size}
    return await sn.send(tenant, notice_id)


@router.post("/{tenant}/notices")
async def create_notice(tenant: str, body: NoticeIn,
                        identity: dict = Depends(require_tenant_admin)) -> dict:
    try:
        row = sn.create(tenant, body.title, body.body, image_url=body.image_url,
                        must_read=body.must_read, audience_spec=body.audience,
                        send_at=None if body.send_now else body.send_at,
                        created_by=identity.get("email"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    merchant_audit.audit(tenant, identity, "staff_notice_created", notice_id=row["id"],
                         title=row["title"], send_now=body.send_now)
    result = None
    if body.send_now:
        result = await _send(tenant, row["id"], len(sn.audience(tenant, body.audience)))
    return {"notice": sn.get(tenant, row["id"]), "result": result}


@router.post("/{tenant}/notices/{notice_id}/send")
async def send_notice(tenant: str, notice_id: str,
                      identity: dict = Depends(require_tenant_admin)) -> dict:
    notice = sn.get(tenant, notice_id)
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found.")
    merchant_audit.audit(tenant, identity, "staff_notice_sent", notice_id=notice_id)
    return await _send(tenant, notice_id, len(sn.audience(tenant, notice.get("audience"))))


@router.post("/{tenant}/notices/{notice_id}/test")
async def test_notice(tenant: str, notice_id: str, authorization: str = Header(default=""),
                      identity: dict = Depends(require_tenant_admin)) -> dict:
    notice = sn.get(tenant, notice_id)
    me = _member_of(await member_scope(authorization, tenant), tenant)
    if not notice:
        raise HTTPException(status_code=404, detail="Notice not found.")
    if not me:
        raise HTTPException(status_code=400, detail="Add your WhatsApp number to your team card first.")
    _, err = await sn._deliver(tenant, {**notice, "id": f"{notice_id}:test"}, me, sn._business(tenant))
    return {"sent_to": me.get("name") or me["whatsapp"], "error": err}


@router.post("/{tenant}/notices/{notice_id}/cancel")
async def cancel_notice(tenant: str, notice_id: str,
                        identity: dict = Depends(require_tenant_admin)) -> dict:
    ok = sn.cancel(tenant, notice_id)
    if ok:
        merchant_audit.audit(tenant, identity, "staff_notice_cancelled", notice_id=notice_id)
    return {"cancelled": ok, "notice": sn.get(tenant, notice_id)}


@router.post("/{tenant}/notices/{notice_id}/ack")
async def ack_notice(tenant: str, notice_id: str, authorization: str = Header(default="")) -> dict:
    scope = await member_scope(authorization, tenant)
    if scope is None:
        raise HTTPException(status_code=403, detail="You don't have access to this workspace.")
    me = _member_of(scope, tenant)
    if not me:
        raise HTTPException(status_code=400, detail="Your WhatsApp number isn't on your team card.")
    sn.acknowledge_member(tenant, me["id"], notice_id)
    mine = [r for r in sn.recipients(tenant, notice_id) if str(r.get("member_id")) == str(me["id"])]
    return {"acknowledged_at": (mine[0].get("acknowledged_at") if mine else None)}
