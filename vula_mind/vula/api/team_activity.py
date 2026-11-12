"""
vula/api/team_activity.py — Team › Activity: what each person has on and has done (vula/team_activity.py).

    GET /v1/team/{tenant}/activity/available          should the tab show for this business?
    GET /v1/team/{tenant}/activity?period=&member_id=  counts per person, or one person's list

Owners and managers only (require_tenant_admin): a staff member sees their own week on WhatsApp
("how does my week look?"). The tab shows only where it fits — more than one person on the team
and a business that works with meetings, visits, bookings or follow-ups — so a one-person shop
never sees an empty page.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from vula import team_activity as ta
from vula.api.tenant_auth import require_tenant_admin

log = logging.getLogger(__name__)
router = APIRouter(tags=["team-activity"])

PERIODS = {"this_week": "this week", "last_week": "last week", "next_week": "next week",
           "this_month": "this month", "last_month": "last month", "today": "today"}
_FITS_TYPES = {"rep", "services", "trades", "health"}
_FITS_MODULES = {"crm", "followups", "bookings", "fieldops"}


def fits(tenant_id: str) -> bool:
    try:
        from vula.api.tenants import enabled_modules, get_config
        btype = (get_config(tenant_id) or {}).get("business_type")
        mods = set(enabled_modules(tenant_id))
    except Exception:
        return False
    return len(ta.members(tenant_id)) > 1 and (btype in _FITS_TYPES or bool(mods & _FITS_MODULES))


@router.get("/{tenant}/activity/available")
async def activity_available(tenant: str) -> dict:
    return {"available": fits(tenant)}


@router.get("/{tenant}/activity")
async def activity(tenant: str, period: str = Query("this_week"), member_id: Optional[str] = None,
                   identity: dict = Depends(require_tenant_admin)) -> dict:
    if period not in PERIODS:
        raise HTTPException(status_code=400, detail="period must be one of " + ", ".join(PERIODS))
    since, until, label = ta.period(PERIODS[period])
    out = {"period": period, "label": label, "since": since.date().isoformat(),
           "until": until.date().isoformat()}
    if not member_id:
        return {**out, "people": ta.team_summary(tenant, since, until)}
    member = next((m for m in ta.members(tenant) if str(m.get("id")) == member_id), None)
    if not member:
        raise HTTPException(status_code=404, detail="Not on the team list.")
    p = ta.person_week(tenant, member, since, until)
    diary = [{"kind": kind, "at": t.isoformat() if t else None,
              "title": (f"Meeting with {r.get('with_name')}" if kind == "meeting"
                        else f"{r.get('service_name') or 'Booking'}" + (f" — {r['customer_name']}" if r.get("customer_name") else "")),
              "address": r.get("address"),
              "checked_in": ta.checkin_note(p["checked_in"].get(r.get("id"))).replace(" · ", "", 1) if kind == "meeting" else ""}
             for kind, t, r in p["diary"]]
    return {**out, "name": p["name"], "diary": diary,
            "notes": [{"at": r.get("created_at"), "summary": r.get("summary")} for r in p["notes"]],
            "follow_ups_done": [{"at": r.get("completed_at"), "text": r.get("text")} for r in p["done"]],
            "overdue": [{"due": r.get("due_at"), "text": r.get("text")} for r in p["overdue"]],
            "contacts_added": [{"at": r.get("created_at"), "name": r.get("name")} for r in p["contacts"]],
            "call_sheet": [{"at": r.get("created_at"), "text": r.get("text")} for r in p["calls"]],
            "sales": {k: {"count": len(p[k]), "cents": sum(int(r.get("total_cents") or 0) for r in p[k])}
                      for k in ("quotes", "invoices", "orders")},
            "check_ins": [{"at": c.get("at"), "place": c.get("place"), "distance_m": c.get("distance_m"),
                           "photos": c.get("photos") or 0, "lat": c.get("lat"), "lng": c.get("lng")}
                          for c in p["checkins"]]}
