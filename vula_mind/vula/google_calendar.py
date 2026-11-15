"""
vula/google_calendar.py — a team member's own Google Calendar, into their Vula diary.

15 Nov 2026: the Google twin of the personal Outlook calendar (vula/outlook_calendar.py). Every
15 minutes (the same loop) the next 7 days of each connected person's primary Google calendar come
into vula_meetings (source 'google', external_id 'google:<event id>'), through the same
_sync_into as Outlook: new events added, moved/renamed updated, cancelled/deleted marked cancelled.
All-day events are skipped; a video-call "location" isn't an address. Reminders only if the person
asked (their calendar_remind_minutes, shared with Outlook).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from vula.when import now_sast

log = logging.getLogger(__name__)

_EVENTS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"


async def fetch(access_token: str, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    params = {"timeMin": start.astimezone(timezone.utc).isoformat(), "timeMax": end.astimezone(timezone.utc).isoformat(),
              "singleEvents": "true", "orderBy": "startTime", "maxResults": "100", "showDeleted": "false"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(_EVENTS, params=params, headers={"Authorization": f"Bearer {access_token}"})
        r.raise_for_status()
        return (r.json() or {}).get("items") or []


def to_meeting(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    start = (event.get("start") or {}).get("dateTime")          # all-day events only have "date"
    if not start or not event.get("id"):
        return None
    try:
        at = datetime.fromisoformat(start.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None
    where = (event.get("location") or "").strip()
    if re.match(r"^https?://|^(?:google )?meet\b|^zoom\b|^online$", where, re.I):
        where = ""                                                # nothing to drive to
    return {"external_id": f"google:{event['id']}", "with_name": (event.get("summary") or "Meeting").strip()[:200],
            "address": where[:300] or None, "starts_at": at.isoformat(),
            "status": "cancelled" if event.get("status") == "cancelled" else "scheduled"}


async def sync_member(tenant_id: str, member_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    from vula import outlook_calendar as oc
    from vula import team_index
    from vula.google import member_credentials as gc
    now = now or now_sast()
    member = next((m for m in team_index.active_members(tenant_id) if str(m.get("id")) == str(member_id)), None)
    if not member or not member.get("whatsapp"):
        return {"skipped": "not an active team member with WhatsApp"}
    creds = await gc.get_token(tenant_id, member_id)
    if not creds:
        gc.mark(tenant_id, member_id, "Couldn't sign in to the calendar — connect it again")
        return {"skipped": "no token"}
    out = await oc._sync_into(tenant_id, creds["access_token"], member, now,
                              source="google", fetcher=fetch, mapper=to_meeting)
    gc.mark(tenant_id, member_id, out.get("error"))
    return out


async def sync_all() -> Dict[str, Any]:
    from vula.google import member_credentials as gc
    out: Dict[str, Any] = {}
    try:
        people = gc.connections()
    except Exception as exc:
        log.debug("Google calendars skipped (run migration 265?): %s", exc)
        return out
    for c in people:
        if gc.has_calendar(c.get("scopes")):
            out[f"google:{c['tenant_id']}/{c['member_id']}"] = await sync_member(c["tenant_id"], c["member_id"])
    return out
