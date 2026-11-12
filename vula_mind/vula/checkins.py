"""
vula/checkins.py — "I've arrived": a team member checks in at a meeting or site visit.

12 Nov 2026 (Ian): "if a rep gets to a meeting, says I have arrived and takes a photo, will we be
able to pull location details through WhatsApp, time and date stamp?"

  1. "arrived" / "I'm at Acme" / "check in" (commerce_admin meeting tool, action=check_in)
     → a row waiting for the pin, matched to their meeting starting around now, and Vula asks
       for the location: 📎 → Location → Send your current location.
  2. The pin they share (WhatsApp location message, vula/api/whatsapp.py) → checked in:
     WhatsApp's own time on the message (not the phone's clock), the place, and how far it is
     from the meeting's address (geo.geocode + haversine). Far away → said plainly.
     A pin also checks in, without saying "arrived", when one of their meetings starts within
     30 minutes before to 90 minutes after — anything else is left to the normal flow.
  3. Photos they send in the hour after are counted with the visit (read from
     vula_filed_documents, filed_by + image/*, at query time — nothing is copied).

What WhatsApp can and can't prove: photos arrive with their location data stripped, and a
person can pick another spot when sharing a pin — so this is good evidence, not proof. Location
is recorded only at a check-in the person makes; nothing tracks anyone.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from vula.when import SAST, now_sast

log = logging.getLogger(__name__)

TABLE = "vula_checkins"
PENDING_FOR = timedelta(minutes=30)
FAR_M = 500                                      # further than this from the address → said plainly
PHOTO_WINDOW = timedelta(minutes=60)


def _client():
    from vula.commerce import service
    return service._client()


def digits(phone: Optional[str]) -> str:
    return re.sub(r"\D", "", phone or "")


def _ts(v: Any) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except Exception:
        return None


def _meetings_near(tenant_id: str, phone: str, now: datetime, before: timedelta, after: timedelta) -> List[Dict[str, Any]]:
    """This person's scheduled meetings starting between now-before and now+after, closest first."""
    from vula import meetings as mt
    try:
        rows = (_client().table(mt.TABLE).select("id,with_name,address,starts_at,status,created_by")
                .eq("tenant_id", tenant_id).eq("created_by", digits(phone)).eq("status", "scheduled")
                .gte("starts_at", (now - before).isoformat()).lte("starts_at", (now + after).isoformat())
                .execute().data or [])
    except Exception as exc:
        log.debug("check-in meeting lookup skipped: %s", exc)
        return []
    return sorted(rows, key=lambda r: abs(((_ts(r["starts_at"]) or now) - now).total_seconds()))


def _meeting(tenant_id: str, meeting_id: Optional[str]) -> Optional[Dict[str, Any]]:
    if not meeting_id:
        return None
    from vula import meetings as mt
    return mt.get(tenant_id, meeting_id)


# ── 1. "arrived" ─────────────────────────────────────────────────────────────────────────

ASK_PIN = ("Send your location to check in: tap 📎 (or +) → *Location* → *Send your current location*.")


def start(tenant_id: str, phone: str, hint: str = "", now: Optional[datetime] = None) -> Dict[str, Any]:
    """A row waiting for the pin, tied to the meeting they're arriving for (named, or the one
    starting closest to now). Returns {"row", "meeting"}."""
    now = now or now_sast()
    near = _meetings_near(tenant_id, phone, now, timedelta(hours=2), timedelta(hours=3))
    h = (hint or "").strip().lower()
    words = [w for w in re.findall(r"[a-z0-9]+", h) if len(w) > 2 and w not in ("the", "meeting", "with", "site")]
    if words:
        named = [m for m in near if any(w in (m.get("with_name") or "").lower() for w in words)]
        near = named or []
    meeting = near[0] if near else None
    row = {"tenant_id": tenant_id, "person": digits(phone), "meeting_id": (meeting or {}).get("id"),
           "with_hint": None if meeting else (hint.strip()[:120] or None), "status": "awaiting_pin"}
    res = _client().table(TABLE).insert(row).execute()
    return {"row": (res.data or [None])[0], "meeting": meeting}


# ── 2. the pin ───────────────────────────────────────────────────────────────────────────

async def from_pin(tenant_id: str, phone: str, lat: float, lng: float, place: str = "",
                   sent_at: Optional[datetime] = None, now: Optional[datetime] = None) -> Optional[str]:
    """Check in from a shared location pin, or None when this pin isn't a check-in (not on the
    team, no "arrived" in the last 30 minutes and no meeting starting about now) — then the
    normal flow handles it. Returns the reply text."""
    from vula import team_index
    now = now or now_sast()
    if not team_index.member_for_phone(tenant_id, phone):
        return None
    me = digits(phone)
    try:
        pending = (_client().table(TABLE).select("id,meeting_id,with_hint,created_at")
                   .eq("tenant_id", tenant_id).eq("person", me).eq("status", "awaiting_pin")
                   .gte("created_at", (now - PENDING_FOR).isoformat())
                   .order("created_at", desc=True).limit(1).execute().data or [])
    except Exception as exc:
        log.debug("check-in lookup skipped (run migration 259?): %s", exc)
        return None
    meeting = None
    if pending:
        meeting = _meeting(tenant_id, pending[0].get("meeting_id"))
    else:
        near = _meetings_near(tenant_id, phone, now, timedelta(minutes=90), timedelta(minutes=30))
        if not near:
            return None
        meeting = near[0]
    distance_m = None
    if meeting and meeting.get("address"):
        try:
            from vula.commerce import geo
            spot = await geo.geocode(meeting["address"])
            if spot:
                distance_m = int(round(geo.haversine_km(lat, lng, float(spot["lat"]), float(spot["lng"])) * 1000))
        except Exception as exc:
            log.debug("check-in distance skipped: %s", exc)
    at = (sent_at or now).astimezone(timezone.utc)
    record = {"lat": lat, "lng": lng, "place": (place or "").strip()[:200] or None, "distance_m": distance_m,
              "at": at.isoformat(), "status": "checked_in", "meeting_id": (meeting or {}).get("id")}
    try:
        if pending:
            saved = (_client().table(TABLE).update(record).eq("tenant_id", tenant_id)
                     .eq("id", pending[0]["id"]).execute().data or [])
        else:
            saved = (_client().table(TABLE).insert({"tenant_id": tenant_id, "person": me, **record})
                     .execute().data or [])
    except Exception as exc:
        log.warning("check-in save failed for %s: %s", tenant_id, exc)
        saved = []
    if not saved or saved[0].get("status") != "checked_in":
        return "I couldn't save your check-in just now — send the location again in a minute."
    return reply_text(meeting, (pending[0].get("with_hint") if pending else None), at, distance_m, lat, lng, place)


def reply_text(meeting: Optional[Dict[str, Any]], hint: Optional[str], at: datetime,
               distance_m: Optional[int], lat: float, lng: float, place: str = "") -> str:
    from vula.meetings import maps_link
    who = (meeting or {}).get("with_name") or hint
    when = at.astimezone(SAST).strftime("%H:%M on %a %-d %b")
    out = [f"✅ Checked in{' for *' + who + '*' if who else ''} at {when} (WhatsApp's time)."]
    pin = f"https://www.google.com/maps/search/?api=1&query={lat},{lng}"
    if meeting and meeting.get("address"):
        if distance_m is None:
            out.append(f"📍 I couldn't place {meeting['address']} on the map to compare.")
        elif distance_m > FAR_M:
            far = f"{distance_m / 1000:.1f} km" if distance_m >= 1000 else f"{distance_m} m"
            out.append(f"⚠️ This pin is {far} from the meeting address ({meeting['address']}). "
                       "If you're in the right place, the address may need fixing.")
        else:
            out.append(f"📍 {distance_m} m from {meeting['address']}.")
    elif place:
        out.append(f"📍 {place}")
    out.append(pin if not (meeting and meeting.get("address")) else maps_link(meeting["address"]))
    out.append("Send site photos now and they'll be kept with this visit.")
    return "\n".join(out)


# ── 3. reading them back ─────────────────────────────────────────────────────────────────

def for_period(tenant_id: str, since: datetime, until: datetime, phones: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    try:
        q = (_client().table(TABLE).select("id,person,meeting_id,with_hint,place,distance_m,at,lat,lng")
             .eq("tenant_id", tenant_id).eq("status", "checked_in")
             .gte("at", since.isoformat()).lt("at", until.isoformat()))
        if phones is not None:
            q = q.in_("person", [digits(p) for p in phones])
        return q.order("at").limit(1000).execute().data or []
    except Exception as exc:
        log.debug("check-ins read skipped: %s", exc)
        return []


def photos_after(tenant_id: str, phone: str, at: datetime) -> int:
    """Photos the person filed in the hour after checking in."""
    try:
        from vula.team_activity import phone_variants
        rows = (_client().table("vula_filed_documents").select("id,mime")
                .eq("tenant_id", tenant_id).in_("filed_by", phone_variants(phone))
                .gte("created_at", at.isoformat()).lt("created_at", (at + PHOTO_WINDOW).isoformat())
                .limit(50).execute().data or [])
    except Exception:
        return 0
    return sum(1 for r in rows if str(r.get("mime") or "").startswith("image/"))
