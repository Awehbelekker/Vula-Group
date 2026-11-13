"""
vula/outlook_calendar.py — meetings from the connected Outlook calendar, into the same diary.

12 Nov 2026 (Ian): "let's say they have an appointment in their calendar — will Vula send a
notification and the address to open in maps?" Step 4 of meeting prep, Outlook first:

  • every 15 minutes, the next 7 days of the business's connected Microsoft account
    (Calendars.Read, added to vula/microsoft/service.SCOPES) are read into vula_meetings
    (source 'outlook', external_id = the event id, migration 264) — new events added, moved or
    renamed ones updated, cancelled or deleted ones marked cancelled;
  • they then work like any meeting: "what meetings do I have?", the Today list, the morning brief,
    team activity, check-ins on arrival, "brief me on…";
  • a reminder (with the address and a Google Maps link, and a brief if wanted) only for someone
    who asked: "remind me 30 minutes before my Outlook meetings" (calendar_remind_minutes /
    calendar_brief on their team row). Nothing is sent otherwise.

The business's Microsoft account's meetings belong to the team member with that email (else the
owner). Since 13 Nov 2026 each person can also connect their own calendar (sync_member,
vula/microsoft/member_credentials.py); the business calendar then skips them, so no meeting comes
in twice. Google is a later step (its calendar permission needs Google's own review). A connection made before the
calendar permission existed keeps working for mail and gets the calendar when it reconnects.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from vula.when import now_sast

log = logging.getLogger(__name__)

WINDOW = timedelta(days=7)
_GRAPH = "https://graph.microsoft.com/v1.0"


def _client():
    from vula.commerce import service
    return service._client()


def has_calendar(scopes: Optional[str]) -> bool:
    return "calendars.read" in (scopes or "").lower()


def digits(phone: Optional[str]) -> str:
    return re.sub(r"\D", "", phone or "")


def owner_of(tenant_id: str, account_email: Optional[str]) -> Optional[Dict[str, Any]]:
    """The team member whose email is the connected account's, else the first owner with WhatsApp."""
    from vula import team_index
    team = [m for m in team_index.active_members(tenant_id) if m.get("whatsapp")]
    email = (account_email or "").strip().lower()
    match = next((m for m in team if email and (m.get("email") or "").strip().lower() == email), None)
    return match or next((m for m in team if (m.get("role") or "") == "owner"), None)


def _when(part: Optional[Dict[str, Any]]) -> Optional[datetime]:
    """Graph's {"dateTime": "2026-11-13T08:00:00.0000000", "timeZone": "UTC"} → aware UTC."""
    raw = (part or {}).get("dateTime")
    if not raw:
        return None
    try:
        t = datetime.fromisoformat(raw[:19])
    except ValueError:
        return None
    return t.replace(tzinfo=timezone.utc)


def to_meeting(event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A calendar event → the fields of a vula_meetings row, or None (all-day, no start)."""
    if event.get("isAllDay"):
        return None
    start = _when(event.get("start"))
    if not start:
        return None
    loc = event.get("location") or {}
    addr = loc.get("address") or {}
    street = ", ".join(x for x in (addr.get("street"), addr.get("city")) if x)
    where = (loc.get("displayName") or street or "").strip()
    if re.match(r"^(?:microsoft )?teams meeting$|^online$", where, re.I):
        where = ""                                     # nothing to drive to
    return {"external_id": f"outlook:{event['id']}", "with_name": (event.get("subject") or "Meeting").strip()[:200],
            "address": where[:300] or None, "starts_at": start.isoformat(),
            "status": "cancelled" if event.get("isCancelled") else "scheduled"}


async def fetch(access_token: str, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    params = {"startDateTime": start.astimezone(timezone.utc).isoformat(),
              "endDateTime": end.astimezone(timezone.utc).isoformat(),
              "$select": "id,subject,start,end,location,isCancelled,isAllDay", "$top": "100",
              "$orderby": "start/dateTime"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(f"{_GRAPH}/me/calendarView", params=params,
                             headers={"Authorization": f"Bearer {access_token}", "Prefer": 'outlook.timezone="UTC"'})
        r.raise_for_status()
        return (r.json() or {}).get("value") or []


async def sync(tenant_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The business's connected calendar into the diary of the member it belongs to. Never raises.
    Skipped for someone who connected their own calendar (sync_member) — no meeting twice."""
    from vula.microsoft.credentials import get_access_token
    now = now or now_sast()
    try:
        creds = await get_access_token(tenant_id)
    except Exception as exc:
        return {"skipped": f"no token: {exc}"}
    if not creds or not has_calendar(creds.get("scopes")):
        return {"skipped": "not connected with calendar access — reconnect Microsoft"}
    member = owner_of(tenant_id, creds.get("email"))
    if not member:
        return {"skipped": "nobody on the team with WhatsApp to give the meetings to"}
    if str(member.get("id")) in _personally_connected(tenant_id):
        return {"skipped": f"{member.get('name')} reads their own calendar"}
    return await _sync_into(tenant_id, creds["access_token"], member, now)


async def sync_member(tenant_id: str, member_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """One person's own connected calendar into their own diary (vula/microsoft/member_credentials)."""
    from vula import team_index
    from vula.microsoft import member_credentials as mc
    now = now or now_sast()
    member = next((m for m in team_index.active_members(tenant_id) if str(m.get("id")) == str(member_id)), None)
    if not member or not member.get("whatsapp"):
        return {"skipped": "not an active team member with WhatsApp"}
    creds = await mc.get_token(tenant_id, member_id)
    if not creds:
        mc.mark(tenant_id, member_id, "Couldn't sign in to the calendar — connect it again")
        return {"skipped": "no token"}
    out = await _sync_into(tenant_id, creds["access_token"], member, now)
    mc.mark(tenant_id, member_id, out.get("error"))
    return out


def _personally_connected(tenant_id: str) -> set:
    try:
        from vula.microsoft import member_credentials as mc
        return {str(c["member_id"]) for c in mc.connections(tenant_id)}
    except Exception:
        return set()


async def _sync_into(tenant_id: str, access_token: str, member: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """The next 7 days of one calendar into one person's diary: add, update, cancel what's gone.
    Only this person's Outlook meetings are compared, so one calendar never touches another's."""
    try:
        events = await fetch(access_token, now, now + WINDOW)
    except Exception as exc:
        log.warning("Outlook calendar read failed for %s: %s", tenant_id, exc)
        return {"error": str(exc)[:200]}
    db = _client()
    person = digits(member["whatsapp"])
    try:                                                 # their own reminder choice (migration 264)
        prefs = (db.table("vula_team_members").select("calendar_remind_minutes,calendar_brief")
                 .eq("tenant_id", tenant_id).eq("id", member["id"]).limit(1).execute().data or [{}])[0]
    except Exception:
        prefs = {}
    existing = {r["external_id"]: r for r in (db.table("vula_meetings")
                .select("id,external_id,with_name,address,starts_at,status")
                .eq("tenant_id", tenant_id).eq("source", "outlook").eq("created_by", person)
                .gte("starts_at", now.isoformat()).lte("starts_at", (now + WINDOW).isoformat())
                .execute().data or []) if r.get("external_id")}
    added = updated = cancelled = 0
    seen = set()
    for ev in events:
        row = to_meeting(ev)
        if not row:
            continue
        seen.add(row["external_id"])
        old = existing.get(row["external_id"])
        if old:
            patch = {k: v for k, v in row.items() if k != "external_id" and str(old.get(k) or "") != str(v or "")}
            if "starts_at" in patch and old.get("starts_at") and \
                    datetime.fromisoformat(str(old["starts_at"]).replace("Z", "+00:00")) == datetime.fromisoformat(row["starts_at"]):
                patch.pop("starts_at")                  # same moment, written differently
            if patch:
                if "starts_at" in patch:
                    patch["reminded_at"] = None         # the reminder goes again for the new time
                patch["updated_at"] = datetime.now(timezone.utc).isoformat()
                db.table("vula_meetings").update(patch).eq("id", old["id"]).execute()
                updated += 1
            continue
        if row["status"] == "cancelled":
            continue
        db.table("vula_meetings").insert({
            **row, "tenant_id": tenant_id, "created_by": person, "source": "outlook",
            "remind_minutes": prefs.get("calendar_remind_minutes") or None,
            "with_brief": bool(prefs.get("calendar_brief"))}).execute()
        added += 1
    for ext, old in existing.items():                   # gone from the calendar → cancelled
        if ext not in seen and old.get("status") != "cancelled":
            db.table("vula_meetings").update({"status": "cancelled"}).eq("id", old["id"]).execute()
            cancelled += 1
    return {"added": added, "updated": updated, "cancelled": cancelled, "for": member.get("name")}


async def sync_all() -> Dict[str, Any]:
    """Every business calendar, then every person's own one."""
    out: Dict[str, Any] = {}
    try:
        rows = (_client().table("vula_microsoft_accounts").select("tenant_id,scopes")
                .eq("status", "connected").execute().data or [])
    except Exception as exc:
        log.debug("calendar sync skipped: %s", exc)
        rows = []
    for r in rows:
        if r.get("tenant_id") and has_calendar(r.get("scopes")):
            out[r["tenant_id"]] = await sync(r["tenant_id"])
    try:
        from vula.microsoft import member_credentials as mc
        people = mc.connections()
    except Exception as exc:
        log.debug("personal calendars skipped (run migration 265?): %s", exc)
        people = []
    for c in people:
        if has_calendar(c.get("scopes")):
            out[f"{c['tenant_id']}/{c['member_id']}"] = await sync_member(c["tenant_id"], c["member_id"])
    return out


def disconnect_member(tenant_id: str, member_id: str, now: Optional[datetime] = None) -> bool:
    """Forget this person's calendar connection; their future meetings from it are cancelled
    (past ones stay, for their activity history). True when it's gone, as read back."""
    from vula import team_index
    from vula.microsoft import member_credentials as mc
    now = now or now_sast()
    member = next((m for m in team_index.active_members(tenant_id) if str(m.get("id")) == str(member_id)), None)
    gone = mc.disconnect(tenant_id, member_id)
    if gone and member and member.get("whatsapp"):
        _client().table("vula_meetings").update({"status": "cancelled"}).eq("tenant_id", tenant_id) \
            .eq("source", "outlook").eq("created_by", digits(member["whatsapp"])) \
            .gte("starts_at", now.isoformat()).execute()
    return gone


def set_reminders(tenant_id: str, phone: str, minutes: Optional[int], brief: Optional[bool] = None,
                  now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """This person's choice for their calendar meetings, applied to the ones already synced too.
    Returns their team row as read back, or None if they aren't on the team."""
    from vula import team_index
    member = team_index.member_for_phone(tenant_id, phone)
    if not member:
        return None
    patch: Dict[str, Any] = {"calendar_remind_minutes": minutes or None}
    if brief is not None:
        patch["calendar_brief"] = bool(brief)
    db = _client()
    db.table("vula_team_members").update(patch).eq("tenant_id", tenant_id).eq("id", member["id"]).execute()
    now = now or now_sast()
    future = {"remind_minutes": minutes or None, "reminded_at": None}
    if brief is not None:
        future["with_brief"] = bool(brief)
    db.table("vula_meetings").update(future).eq("tenant_id", tenant_id).eq("source", "outlook") \
        .eq("created_by", digits(member["whatsapp"])).gte("starts_at", now.isoformat()).execute()
    team_index.invalidate(tenant_id)
    back = (db.table("vula_team_members").select("id,calendar_remind_minutes,calendar_brief")
            .eq("tenant_id", tenant_id).eq("id", member["id"]).limit(1).execute().data or [None])[0]
    return back
