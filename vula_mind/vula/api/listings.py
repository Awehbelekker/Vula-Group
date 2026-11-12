"""
vula/api/listings.py — property listings for real-estate businesses (vula/listings/service.py).

Mounted under /v1/commerce, so /v1/commerce/{tenant}/admin/... is behind the tenant guard
(server._TENANT_GUARD_RES) and the rest is the public website side.

Public (the business's website / page-builder blocks):
    GET  /v1/commerce/{tenant}/properties            ?type=sale|rent&property_type=&min_price=&max_price=
                                                      &beds=&suburb=&featured=1&include_sold=1&limit=
    GET  /v1/commerce/{tenant}/properties/{slug}
    GET  /v1/commerce/{tenant}/property-agents
    POST /v1/commerce/{tenant}/property-enquiries     rate-limited, honeypot field `website`

Admin (dashboard):
    GET/POST          /v1/commerce/{tenant}/admin/properties
    GET/DELETE        /v1/commerce/{tenant}/admin/properties/{id}
    GET/POST          /v1/commerce/{tenant}/admin/property-agents
    DELETE            /v1/commerce/{tenant}/admin/property-agents/{id}
    GET               /v1/commerce/{tenant}/admin/property-enquiries   ?status=
    POST              /v1/commerce/{tenant}/admin/property-enquiries/{id}/status
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from vula.listings import service as ls

log = logging.getLogger(__name__)
router = APIRouter(tags=["listings"])

MAX_ENQUIRIES_PER_IP = 5   # per hour, per business


def _ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "")) or "?"


def _clean(v: Optional[str], n: int) -> str:
    return re.sub(r"\s+", " ", (v or "")).strip()[:n]


def _rands(v: Optional[str]) -> Optional[int]:
    if v in (None, ""):
        return None
    cents = ls.parse_rands_to_cents(v)
    if cents is None:
        raise HTTPException(status_code=422, detail=f"Couldn't read the price '{v}'.")
    return cents


# ── Public ────────────────────────────────────────────────────────────────────

@router.get("/{tenant_id}/properties")
async def public_list(
    tenant_id: str,
    type: Optional[str] = Query(None, description="sale | rent"),
    property_type: Optional[str] = None,
    min_price: Optional[str] = Query(None, description="rands, e.g. 1500000 or R1.5m"),
    max_price: Optional[str] = None,
    beds: Optional[float] = None,
    suburb: Optional[str] = None,
    featured: bool = False,
    include_sold: bool = False,
    limit: int = 60,
) -> Dict[str, Any]:
    rows = await ls.public_properties(
        tenant_id, include_sold=include_sold, listing_type=type, property_type=property_type,
        min_price_cents=_rands(min_price), max_price_cents=_rands(max_price), min_bedrooms=beds,
        suburb=suburb, featured_only=featured, limit=min(max(limit, 1), 200))
    return {"tenant_id": tenant_id, "properties": rows, "count": len(rows)}


@router.get("/{tenant_id}/properties/{slug}")
async def public_detail(tenant_id: str, slug: str) -> Dict[str, Any]:
    prop = await ls.public_property(tenant_id, slug)
    if not prop:
        raise HTTPException(status_code=404, detail="Listing not found")
    return prop


@router.get("/{tenant_id}/property-agents")
async def public_agents(tenant_id: str) -> Dict[str, Any]:
    agents = await ls.list_agents(tenant_id)
    keep = ("id", "name", "title", "whatsapp", "email", "photo_url", "bio", "ppra_ffc")
    return {"agents": [{k: a.get(k) for k in keep} for a in agents]}


class EnquiryIn(BaseModel):
    name: str
    whatsapp: Optional[str] = None
    email: Optional[str] = None
    message: Optional[str] = None
    property_slug: Optional[str] = None
    wants_viewing: bool = False
    website: Optional[str] = None      # honeypot — hidden on the page; people leave it empty


@router.post("/{tenant_id}/property-enquiries")
async def create_enquiry(tenant_id: str, body: EnquiryIn, request: Request) -> Dict[str, Any]:
    if body.website:                                       # a bot filled the hidden field
        return {"ok": True}
    from vula.team_index import digits_za
    name = _clean(body.name, 80)
    phone = digits_za(body.whatsapp or "")
    email = _clean(body.email, 120)
    if not name:
        raise HTTPException(status_code=422, detail="Please add your name.")
    if phone and not (9 <= len(phone) <= 15):
        raise HTTPException(status_code=422, detail="Please check your WhatsApp number, e.g. 082 123 4567.")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=422, detail="Please check your email address.")
    if not phone and not email:
        raise HTTPException(status_code=422, detail="Please add a WhatsApp number or email so the agent can reach you.")
    ip = _ip(request)
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    try:
        recent = (ls._client().table(ls.ENQUIRIES).select("id").eq("tenant_id", tenant_id)
                  .eq("ip", ip).gte("created_at", since).execute().data or [])
    except Exception:
        recent = []
    if len(recent) >= MAX_ENQUIRIES_PER_IP:
        raise HTTPException(status_code=429, detail="Thanks — the agent already has your details.")
    try:
        await ls.create_enquiry(tenant_id, {
            "name": name, "whatsapp": phone or None, "email": email or None,
            "message": _clean(body.message, 1000) or None,
            "property_slug": _clean(body.property_slug, 80) or None,
            "wants_viewing": body.wants_viewing,
        }, ip=ip)
    except LookupError:
        raise HTTPException(status_code=404, detail="That listing is no longer available.")
    except Exception as exc:
        log.error("property enquiry not stored (%s): %s", tenant_id, exc)
        raise HTTPException(status_code=503, detail="Couldn't send that just now — please try again.")
    return {"ok": True}


# ── Admin ─────────────────────────────────────────────────────────────────────

@router.get("/{tenant_id}/admin/properties")
async def admin_list(tenant_id: str, status: Optional[str] = None) -> Dict[str, Any]:
    statuses = (status,) if status in ls.STATUSES else None
    return {"properties": await ls.list_properties(tenant_id, statuses=statuses, limit=500)}


@router.get("/{tenant_id}/admin/properties/{property_id}")
async def admin_get(tenant_id: str, property_id: str) -> Dict[str, Any]:
    prop = await ls.get_property(tenant_id, property_id=property_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Listing not found")
    return prop


@router.post("/{tenant_id}/admin/properties")
async def admin_upsert(tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return {"property": await ls.upsert_property(tenant_id, body or {})}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except LookupError:
        raise HTTPException(status_code=404, detail="Listing not found")


@router.delete("/{tenant_id}/admin/properties/{property_id}")
async def admin_delete(tenant_id: str, property_id: str) -> Dict[str, Any]:
    await ls.delete_property(tenant_id, property_id)
    return {"ok": True}


@router.get("/{tenant_id}/admin/property-agents")
async def admin_agents(tenant_id: str) -> Dict[str, Any]:
    return {"agents": await ls.list_agents(tenant_id, active_only=False)}


@router.post("/{tenant_id}/admin/property-agents")
async def admin_upsert_agent(tenant_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return {"agent": await ls.upsert_agent(tenant_id, body or {})}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except LookupError:
        raise HTTPException(status_code=404, detail="Agent not found")


@router.delete("/{tenant_id}/admin/property-agents/{agent_id}")
async def admin_delete_agent(tenant_id: str, agent_id: str) -> Dict[str, Any]:
    await ls.delete_agent(tenant_id, agent_id)
    return {"ok": True}


@router.get("/{tenant_id}/admin/property-enquiries")
async def admin_enquiries(tenant_id: str, status: Optional[str] = None) -> Dict[str, Any]:
    return {"enquiries": await ls.list_enquiries(tenant_id, status=status)}


class EnquiryStatusIn(BaseModel):
    status: str


@router.post("/{tenant_id}/admin/property-enquiries/{enquiry_id}/status")
async def admin_enquiry_status(tenant_id: str, enquiry_id: str, body: EnquiryStatusIn) -> Dict[str, Any]:
    try:
        return {"enquiry": await ls.set_enquiry_status(tenant_id, enquiry_id, body.status)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except LookupError:
        raise HTTPException(status_code=404, detail="Enquiry not found")


# ── Sharing and wish lists (migration 259, vula/listings/sharing.py) ─────────

@router.get("/{tenant_id}/admin/properties/{property_id}/matches")
async def admin_matches(tenant_id: str, property_id: str) -> Dict[str, Any]:
    from vula.listings import sharing
    prop = await ls.get_property(tenant_id, property_id=property_id)
    if not prop:
        raise HTTPException(status_code=404, detail="Listing not found")
    people = await sharing.matches_for(tenant_id, prop)
    return {"matches": [{"id": w["id"], "name": w.get("name"), "phone": w["phone"],
                         "looking_for": sharing.describe_wish(w)} for w in people]}


class ShareIn(BaseModel):
    phones: list
    note: Optional[str] = None


@router.post("/{tenant_id}/admin/properties/{property_id}/share")
async def admin_share(tenant_id: str, property_id: str, body: ShareIn) -> Dict[str, Any]:
    """Send one listing to the people the agent ticked, on WhatsApp. Each send is recorded; a
    person WhatsApp refused (usually: no message from them in the last 24 hours) is reported
    back by name, never counted as sent."""
    from vula.listings import sharing
    prop = await ls.get_property(tenant_id, property_id=property_id)
    if not prop or prop.get("status") not in ls.PUBLIC_STATUSES:
        raise HTTPException(status_code=422, detail="Only a listing on the market can be sent.")
    phones = [p for p in dict.fromkeys(sharing._digits(str(x)) for x in (body.phones or [])) if p][:50]
    if not phones:
        raise HTTPException(status_code=422, detail="Pick at least one client.")
    sent, failed = [], []
    for phone in phones:
        res = await sharing.send_listings(tenant_id, phone, [prop], intro=_clean(body.note, 300),
                                          shared_by="dashboard", source="match")
        (sent if res["ok"] else failed).append(phone)
    return {"sent": sent, "failed": failed}


@router.get("/{tenant_id}/admin/buyer-wishes")
async def admin_wishes(tenant_id: str) -> Dict[str, Any]:
    from vula.listings import sharing
    try:
        rows = (ls._client().table(sharing.WISHES).select("*").eq("tenant_id", tenant_id)
                .eq("active", True).order("updated_at", desc=True).limit(500).execute().data or [])
    except Exception:
        rows = []
    return {"wishes": [{**w, "looking_for": sharing.describe_wish(w)} for w in rows]}


@router.post("/{tenant_id}/admin/buyer-wishes/{wish_id}/close")
async def admin_close_wish(tenant_id: str, wish_id: str) -> Dict[str, Any]:
    from vula.listings import sharing
    res = (ls._client().table(sharing.WISHES).update({"active": False})
           .eq("tenant_id", tenant_id).eq("id", wish_id).execute())
    if not res.data:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}
