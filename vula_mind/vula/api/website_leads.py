"""
vula/api/website_leads.py — the sign-up form on Vula's own website (vula-ai.com).

15 Oct 2026 (Ian: the website's form "goes nowhere" until wired). A prospect leaves their name,
business, WhatsApp number and what they do; the lead is stored (migration 213) and the Vula team
is told straight away on WhatsApp — the owners of the `vula` business once it exists, else
settings.team_whatsapp — and by email when TEAM_EMAIL is set. Public by design: rate-limited per
address, with a hidden honeypot field that bots fill and people don't. Master can list leads.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from config import settings

log = logging.getLogger(__name__)
router = APIRouter(tags=["website"])
from vula.api.master_auth import require_master  # noqa: E402

master_router = APIRouter(tags=["website"], dependencies=[Depends(require_master)])

_TABLE = "vula_website_leads"
MAX_PER_IP = 5            # per hour
VULA_TENANT = "vula"
_KINDS = {"shop / food", "i make things", "construction / design", "brand rep", "services", "something else"}


def _db():
    from vula.commerce import service
    return service._client()


class LeadIn(BaseModel):
    name: str
    business: str
    whatsapp: str
    kind: Optional[str] = None
    source: Optional[str] = None
    website: Optional[str] = None      # honeypot — hidden on the page; people leave it empty


def _clean(v: Optional[str], n: int) -> str:
    return re.sub(r"\s+", " ", (v or "")).strip()[:n]


def _digits(phone: str) -> str:
    from vula.team_index import digits_za
    return digits_za(phone or "")


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")) or "?"


def _recipients() -> List[str]:
    """Who hears about a new lead: the Vula business's owners, else the team number."""
    try:
        from vula.team_index import active_members
        owners = [m["whatsapp"] for m in active_members(VULA_TENANT)
                  if (m.get("role") or "") in ("owner", "manager") and m.get("whatsapp")]
        if owners:
            return owners
    except Exception as exc:
        log.debug("vula team lookup failed: %s", exc)
    return [settings.team_whatsapp] if settings.team_whatsapp and "0000000" not in settings.team_whatsapp else []


async def _tell_team(lead: Dict[str, Any]) -> None:
    text = (f"🌱 New website lead\n{lead['name']} — {lead['business']}\n"
            f"WhatsApp: {lead['whatsapp']}\nWhat they do: {lead.get('kind') or 'not said'}\n\n"
            f"Message them: https://wa.me/{_digits(lead['whatsapp'])}")
    try:
        from vula.api.whatsapp import _send_reply
        for to in _recipients():
            await _send_reply(to, text, VULA_TENANT)
    except Exception as exc:
        log.warning("website lead WhatsApp alert failed: %s", exc)
    if settings.team_email:
        try:
            from vula.api.email import _send
            await _send(settings.team_email, f"New Vula website lead — {lead['business']}",
                        "<p>" + text.replace("\n", "<br>") + "</p>", text)
        except Exception as exc:
            log.warning("website lead email failed: %s", exc)


@router.post("/leads")
async def create_lead(body: LeadIn, request: Request) -> Dict[str, Any]:
    if body.website:                                    # a bot filled the hidden field
        return {"ok": True}
    name, business = _clean(body.name, 80), _clean(body.business, 120)
    phone = _digits(body.whatsapp)
    if not name or not business:
        raise HTTPException(status_code=422, detail="Please add your name and your business.")
    if not (9 <= len(phone) <= 15):
        raise HTTPException(status_code=422, detail="Please add a WhatsApp number we can reach, e.g. 082 123 4567.")
    ip = _ip(request)
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    try:
        recent = _db().table(_TABLE).select("id").eq("ip", ip).gte("created_at", since).execute().data or []
    except Exception:
        recent = []
    if len(recent) >= MAX_PER_IP:
        raise HTTPException(status_code=429, detail="Thanks — we've got your details already.")
    kind = _clean(body.kind, 40)
    lead = {"name": name, "business": business, "whatsapp": phone,
            "kind": kind if kind.lower() in _KINDS else (kind or None),
            "source": _clean(body.source, 80) or "vula-ai.com", "ip": ip}
    try:
        _db().table(_TABLE).insert(lead).execute()
    except Exception as exc:
        log.error("website lead not stored (%s): %s", business, exc)
        raise HTTPException(status_code=503, detail="Couldn't send that just now — please try again.")
    await _tell_team(lead)
    return {"ok": True}


@master_router.get("/leads")
async def list_leads(limit: int = 100) -> Dict[str, Any]:
    rows = (_db().table(_TABLE).select("id,name,business,whatsapp,kind,source,status,created_at")
            .order("created_at", desc=True).limit(min(max(limit, 1), 500)).execute().data or [])
    return {"leads": rows}
