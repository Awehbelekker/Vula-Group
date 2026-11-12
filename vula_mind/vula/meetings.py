"""
vula/meetings.py — a team member's own meetings, booked on WhatsApp, with a reminder only if asked.

12 Nov 2026 (Ian): "let's say they have an appointment … will Vula send a notification and send
the address to open in maps?" Step 1 of meeting prep:

  "Meeting with Acme Builders Thursday 10am at 12 Main Road, Paarden Eiland"
      → booked for the person who said it (vula_meetings, migration 258), read back, and
        confirmed with the address and a Google Maps link
  "remind me an hour before"
      → a WhatsApp at that time with who, when, the address and the map link. No reminder is
        set unless the person asks (Ian: notifications only when requested).
  "what meetings do I have this week?", "move the Acme meeting to 11", "cancel it"

Times are read with vula/when.py (SAST, deterministic, never the model's own arithmetic).
The reminder goes out from the 5-minute automations loop (server._automations_loop).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

from vula.when import SAST, now_sast, say

log = logging.getLogger(__name__)

TABLE = "vula_meetings"
_COLS_BASE = "id,tenant_id,created_by,with_name,contact_phone,address,notes,starts_at,remind_minutes,reminded_at,status"
_COLS = _COLS_BASE + ",with_brief"          # migration 263


def _select(build):
    """Run a select with the brief flag, or without it before migration 263 is applied."""
    try:
        return build(_COLS).execute().data or []
    except Exception:
        return build(_COLS_BASE).execute().data or []


def _client():
    from vula.commerce import service
    return service._client()


def digits(phone: Optional[str]) -> str:
    return re.sub(r"\D", "", phone or "")


def maps_link(address: str) -> str:
    """A link that opens the address in Google Maps on the phone (raw URL — WhatsApp has no
    markdown links)."""
    return "https://www.google.com/maps/search/?api=1&query=" + quote_plus(re.sub(r"\s+", " ", address).strip())


def _at(m: Dict[str, Any]) -> datetime:
    return datetime.fromisoformat(str(m["starts_at"]).replace("Z", "+00:00")).astimezone(SAST)


# ── "1 hour before", "30 min", "the day before" ──────────────────────────────────────────

_LEAD_RE = re.compile(r"\b(\d{1,3}(?:\.\d)?|an?|one|two|three|half an?)\s*(min(?:ute)?s?|hours?|hrs?|h|days?)\b", re.I)
_NO_LEAD_RE = re.compile(r"^\s*(?:no|nope|none|no thanks|no reminder|don'?t)\b", re.I)


def parse_lead(text: Optional[str]) -> Optional[int]:
    """Minutes before the meeting, 0 for "no reminder", None when it can't be read."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if _NO_LEAD_RE.match(t):
        return 0
    if re.search(r"\b(?:the |a )?day before\b", t):
        return 1440
    if re.search(r"\bhalf an? hour\b", t):
        return 30
    m = _LEAD_RE.search(t)
    if not m:
        return None
    n_raw, unit = m.group(1), m.group(2)
    n = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3}.get(n_raw)
    n = 0.5 if n_raw.startswith("half") else n if n is not None else float(n_raw)
    minutes = int(round(n * (1 if unit.startswith("m") else 1440 if unit.startswith("d") else 60)))
    return minutes if 5 <= minutes <= 2880 else None


def lead_text(minutes: int) -> str:
    if minutes % 1440 == 0:
        return "the day before" if minutes == 1440 else f"{minutes // 1440} days before"
    if minutes % 60 == 0:
        return f"{minutes // 60} hour{'s' if minutes != 60 else ''} before"
    return f"{minutes} minutes before"


# ── storage ──────────────────────────────────────────────────────────────────────────────

def get(tenant_id: str, meeting_id: str) -> Optional[Dict[str, Any]]:
    rows = _select(lambda c: _client().table(TABLE).select(c).eq("tenant_id", tenant_id).eq("id", meeting_id).limit(1))
    return rows[0] if rows else None


def create(tenant_id: str, *, created_by: str, with_name: str, starts_at: datetime,
           address: Optional[str] = None, contact_phone: Optional[str] = None,
           notes: Optional[str] = None, remind_minutes: Optional[int] = None,
           with_brief: bool = False) -> Optional[Dict[str, Any]]:
    """Insert and read the row back; None if it didn't land."""
    row = {"tenant_id": tenant_id, "created_by": digits(created_by), "with_name": with_name.strip()[:200],
           "contact_phone": contact_phone, "address": (address or "").strip()[:300] or None,
           "notes": (notes or "").strip()[:1000] or None, "starts_at": starts_at.isoformat(),
           "remind_minutes": remind_minutes or None}
    if with_brief:
        row["with_brief"] = True                       # migration 263
    try:
        res = _client().table(TABLE).insert(row).execute()
    except Exception:
        if not row.pop("with_brief", None):
            raise
        res = _client().table(TABLE).insert(row).execute()
    new_id = (res.data or [{}])[0].get("id")
    return get(tenant_id, new_id) if new_id else None


def update(tenant_id: str, meeting_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Change a meeting and read it back. A new time or reminder clears reminded_at, so the
    reminder goes out again for the new time."""
    patch = dict(patch)
    if "starts_at" in patch or "remind_minutes" in patch:
        patch["reminded_at"] = None
    patch["updated_at"] = datetime.now(timezone.utc).isoformat()
    _client().table(TABLE).update(patch).eq("tenant_id", tenant_id).eq("id", meeting_id).execute()
    return get(tenant_id, meeting_id)


def upcoming(tenant_id: str, phone: str, *, days: int = 14, limit: int = 15,
             now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    now = now or now_sast()
    return _select(lambda c: _client().table(TABLE).select(c).eq("tenant_id", tenant_id)
                   .eq("created_by", digits(phone)).eq("status", "scheduled")
                   .gte("starts_at", (now - timedelta(minutes=30)).isoformat())
                   .lte("starts_at", (now + timedelta(days=days)).isoformat())
                   .order("starts_at").limit(limit))


def find(tenant_id: str, phone: str, hint: str = "", now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """This person's upcoming meetings matching the hint (a name or company) — all of them when
    there's no hint."""
    rows = upcoming(tenant_id, phone, days=120, limit=50, now=now)
    h = (hint or "").strip().lower()
    if not h:
        return rows
    words = [w for w in re.findall(r"[a-z0-9]+", h) if len(w) > 2 and w not in ("the", "meeting", "with")]
    return [r for r in rows if any(w in (r.get("with_name") or "").lower() for w in words)] if words else rows


def clashes(tenant_id: str, phone: str, at: datetime, skip_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Other meetings of this person within an hour either side."""
    try:
        rows = (_client().table(TABLE).select(_COLS_BASE).eq("tenant_id", tenant_id)
                .eq("created_by", digits(phone)).eq("status", "scheduled")
                .gte("starts_at", (at - timedelta(minutes=59)).isoformat())
                .lte("starts_at", (at + timedelta(minutes=59)).isoformat()).execute().data or [])
    except Exception:
        return []
    return [r for r in rows if r.get("id") != skip_id]


def on_day(tenant_id: str, day_start: datetime, day_end: datetime, phone: Optional[str] = None) -> List[Dict[str, Any]]:
    q = (_client().table(TABLE).select(_COLS_BASE).eq("tenant_id", tenant_id).eq("status", "scheduled")
         .gte("starts_at", day_start.isoformat()).lt("starts_at", day_end.isoformat()))
    if phone:
        q = q.eq("created_by", digits(phone))
    return q.order("starts_at").limit(30).execute().data or []


# ── what the person reads ────────────────────────────────────────────────────────────────

def line(m: Dict[str, Any], now: Optional[datetime] = None) -> str:
    return f"*{m['with_name']}* — {say(_at(m), now)}"


def where_lines(m: Dict[str, Any]) -> List[str]:
    if m.get("address"):
        return [f"📍 {m['address']}", maps_link(m["address"])]
    return ["📍 No address yet — send it and I'll add a map link."]


def reminder_text(m: Dict[str, Any], now: Optional[datetime] = None) -> str:
    at = _at(m)
    now = now or now_sast()
    mins = max(0, int((at - now).total_seconds() // 60))
    soon = (f"in {mins} minutes" if mins < 60 else
            f"in {mins // 60} hour{'s' if mins // 60 != 1 else ''}" + (f" {mins % 60} min" if mins % 60 else "")
            if mins < 1440 else say(at, now))
    out = [f"⏰ Meeting {soon}: *{m['with_name']}* at {at.strftime('%H:%M')}"]
    out += where_lines(m)
    if m.get("notes"):
        out.append(f"📝 {m['notes']}")
    out.append("When you get there, say *arrived* and share your location to check in.")
    return "\n".join(out)


# ── sending the reminders ────────────────────────────────────────────────────────────────

async def send_due_reminders(now: Optional[datetime] = None) -> int:
    """Every meeting whose reminder time has come and that hasn't been reminded. Each is claimed
    (reminded_at set where it was still empty) before it's sent, so two workers never both send."""
    now = now or now_sast()
    try:
        rows = _select(lambda c: _client().table(TABLE).select(c).eq("status", "scheduled")
                       .not_.is_("remind_minutes", "null").is_("reminded_at", "null")
                       .gt("starts_at", now.isoformat())
                       .lte("starts_at", (now + timedelta(minutes=2880)).isoformat())
                       .limit(500))
    except Exception as exc:
        log.debug("meeting reminders skipped (run migration 258?): %s", exc)
        return 0
    sent = 0
    for m in rows:
        try:
            if _at(m) - timedelta(minutes=int(m["remind_minutes"])) > now:
                continue
            claimed = (_client().table(TABLE).update({"reminded_at": datetime.now(timezone.utc).isoformat()})
                       .eq("id", m["id"]).is_("reminded_at", "null").execute().data)
            if not claimed:
                continue
            text = reminder_text(m, now)
            if m.get("with_brief"):                     # only when they asked (vula/meeting_brief.py)
                try:
                    from vula import meeting_brief
                    text += "\n\n" + meeting_brief.build(m["tenant_id"], m, m["created_by"])
                except Exception as exc:
                    log.debug("meeting brief skipped: %s", exc)
            from vula import proactive
            where = m.get("address") or "no address saved"
            if await proactive.send(m["tenant_id"], m["created_by"], text, suffix="meeting_reminder",
                                    params=[say(_at(m), now), m["with_name"], where],
                                    idem_key=f"meeting:{m['id']}:{m['starts_at']}"):
                sent += 1
        except Exception as exc:
            log.warning("meeting reminder failed for %s: %s", m.get("id"), exc)
    return sent
