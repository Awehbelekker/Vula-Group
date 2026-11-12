"""
vula/owner_briefs.py — the owner's own WhatsApp briefs: off unless the owner asked, at the time they chose.

  morning   the morning brief (vula/owner_digest.py): what needs a person today, plus money
  weekly    the weekly advisor (vula/owner_advisor.py)

12 Nov 2026 (Ian): "keep notifications off unless it was set on onboarding or asked … each user is
unique, some could find it overwhelming if not requested." So a brief goes out ONLY when this
business switched it on, in onboarding (onboard_wa "briefs" step), by asking on WhatsApp
(commerce_admin owner_briefs tool) or in Team › Notices. Nothing turns it on for everyone. The
owner chooses the time and the days, and Vula asks when they switch it on without saying.

Stored in vula_tenant_config.owner_briefs (migration 257):
  {"morning": {"on": true, "hour": 7, "minute": 0, "days": [0, 1, 2, 3, 4]},
   "weekly":  {"on": true, "day": 0, "hour": 8, "minute": 0}}
Days are 0 = Monday … 6 = Sunday, times SAST.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

KINDS = ("morning", "weekly")
DEFAULTS: Dict[str, Dict[str, Any]] = {
    "morning": {"on": False, "hour": 7, "minute": 0, "days": [0, 1, 2, 3, 4]},
    "weekly": {"on": False, "day": 0, "hour": 8, "minute": 0},
}
WINDOW = {"morning": timedelta(hours=2), "weekly": timedelta(hours=3)}   # a late server still sends
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# What the morning brief can hold (12 Nov 2026, Ian: "can the tenant ask what details they want to
# see … not just a defaulted response"). A business that never chose gets them all.
SECTIONS = ("chats", "follow_ups", "emails", "quotes", "documents", "bookings", "meetings",
            "paid", "overdue", "due_soon", "tax")
SECTION_LABELS = {"chats": "customers waiting", "follow_ups": "follow-ups", "emails": "emails to answer",
                  "quotes": "quotes to approve", "documents": "documents to check", "bookings": "bookings",
                  "meetings": "meetings", "paid": "money paid", "overdue": "overdue", "due_soon": "due this week",
                  "tax": "tax dates"}
GROUPS = {"money": ("paid", "overdue", "due_soon", "tax"),
          "work": ("chats", "follow_ups", "emails", "quotes", "documents", "bookings", "meetings"),
          "diary": ("bookings", "meetings")}
_WORDS = {"chats": ("chat", "customer", "waiting"), "follow_ups": ("follow",), "emails": ("email", "mail"),
          "quotes": ("quote",), "documents": ("document", "doc", "paper"), "bookings": ("booking", "appointment"),
          "meetings": ("meeting",), "paid": ("paid", "payment", "came in"), "overdue": ("overdue", "owe", "debtor"),
          "due_soon": ("due", "upcoming"), "tax": ("tax", "vat", "sars")}


def sections_from_words(words: Any) -> List[str]:
    """['money', 'meetings'] or 'money and meetings' → the section ids, in their usual order."""
    items = words if isinstance(words, list) else re.split(r",|\band\b|&|\+", str(words or ""))
    picked = set()
    for w in items:
        w = str(w).strip().lower().replace("-", "_").replace(" ", "_")
        if not w:
            continue
        if w in SECTIONS:
            picked.add(w)
            continue
        if w.strip("_") in GROUPS:
            picked.update(GROUPS[w.strip("_")])
            continue
        plain = w.replace("_", " ")
        for g, members_ in GROUPS.items():
            if g in plain:
                picked.update(members_)
        for sid, keys in _WORDS.items():
            if any(re.search(rf"\b{re.escape(k)}", plain) for k in keys):   # "overdue" isn't "due"
                picked.add(sid)
    return [x for x in SECTIONS if x in picked]


def apply_sections(d: Dict[str, Any], sections: Optional[List[str]]) -> Dict[str, Any]:
    """The brief's data with the sections the business didn't choose taken out (all when None)."""
    if not sections:
        return d
    keep = set(sections)
    t = dict(d.get("today") or {})
    for sid, keys in (("chats", ("waiting",)), ("follow_ups", ("follow_ups", "follow_ups_overdue")),
                      ("emails", ("emails", "emails_urgent")), ("quotes", ("quotes", "quotes_cents")),
                      ("documents", ("documents",)), ("bookings", ("bookings",)), ("meetings", ("meetings",))):
        if sid not in keep:
            for k in keys:
                t.pop(k, None)
    out = {**d, "today": t}
    if "paid" not in keep:
        out["paid"] = []
    if "overdue" not in keep:
        out["overdue"] = []
    if "due_soon" not in keep:
        out["upcoming_cents"], out["upcoming_n"] = 0, 0
    if "tax" not in keep:
        out["tax"] = []
    return out
_DAY_FULL = ("Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays", "Sundays")


# ── reading and writing ────────────────────────────────────────────────────────────────

def _clean(kind: str, raw: Any) -> Dict[str, Any]:
    """Only valid fields; anything odd is dropped (then the default shows)."""
    if isinstance(raw, bool):                       # an early {"morning": true} shape
        raw = {"on": raw}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, Any] = {}
    if isinstance(raw.get("on"), bool):
        out["on"] = raw["on"]
    h, m = raw.get("hour"), raw.get("minute")
    if isinstance(h, int) and not isinstance(h, bool) and 5 <= h <= 21:
        out["hour"] = h
    if isinstance(m, int) and not isinstance(m, bool) and 0 <= m <= 59:
        out["minute"] = m
    if kind == "morning":
        days = raw.get("days")
        if isinstance(days, list):
            ok = sorted({d for d in days if isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6})
            if ok:
                out["days"] = ok
        secs = raw.get("sections")
        if isinstance(secs, list):
            ok = [x for x in SECTIONS if x in {str(v) for v in secs}]
            if ok:
                out["sections"] = ok
    else:
        d = raw.get("day")
        if isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6:
            out["day"] = d
    return out


def saved_for(tenant_id: str) -> Dict[str, Dict[str, Any]]:
    """Exactly what this business chose — nothing for a brief it never touched."""
    try:
        from vula.api.tenants import get_config
        raw = get_config(tenant_id).get("owner_briefs") or {}
    except Exception as exc:
        log.debug("owner brief settings unread (run migration 257?): %s", exc)
        raw = {}
    if not isinstance(raw, dict):
        return {}
    out = {k: _clean(k, raw.get(k)) for k in KINDS}
    return {k: v for k, v in out.items() if v}


def settings_for(tenant_id: str) -> Dict[str, Any]:
    saved = saved_for(tenant_id)
    out: Dict[str, Any] = {}
    for k in KINDS:
        cfg = {**DEFAULTS[k], **saved.get(k, {})}
        cfg["when"] = describe(k, cfg)
        if k == "morning":
            cfg["sections"] = cfg.get("sections") or list(SECTIONS)
            cfg["sections_chosen"] = "sections" in saved.get(k, {})
        cfg["time_chosen"] = "hour" in saved.get(k, {})
        out[k] = cfg
    return out


def enabled(tenant_id: str, kind: str) -> bool:
    return bool(saved_for(tenant_id).get(kind, {}).get("on"))


def save(tenant_id: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge {kind: {on/hour/minute/days/day}} into what's saved and read it back. A kind set to
    None is forgotten (off, default time)."""
    from vula.api import tenants as _t
    new = saved_for(tenant_id)
    for k in KINDS:
        if k not in (patch or {}):
            continue
        if patch[k] is None:
            new.pop(k, None)
            continue
        merged = _clean(k, {**new.get(k, {}), **_clean(k, patch[k])})
        if merged:
            new[k] = merged
    (_t._client().table("vula_tenant_config").update({"owner_briefs": new or None})
     .eq("tenant_id", tenant_id).execute())
    _t.invalidate(tenant_id)
    return settings_for(tenant_id)


# ── when ───────────────────────────────────────────────────────────────────────────────

def describe(kind: str, cfg: Dict[str, Any]) -> str:
    at = f"{cfg.get('hour', 7):02d}:{cfg.get('minute', 0):02d}"
    if kind == "weekly":
        return f"{_DAY_FULL[cfg.get('day', 0)]} at {at}"
    days = cfg.get("days") or []
    if days == [0, 1, 2, 3, 4]:
        which = "weekdays"
    elif days == list(range(7)):
        which = "every day"
    elif len(days) > 2 and days == list(range(days[0], days[-1] + 1)):
        which = f"{DAY_NAMES[days[0]]}–{DAY_NAMES[days[-1]]}"
    else:
        which = ", ".join(DAY_NAMES[d] for d in days)
    return f"{which} at {at}"


def due(kind: str, cfg: Dict[str, Any], now: datetime) -> bool:
    """On, today is one of its days, and it's between the chosen time and the end of the window."""
    if not cfg.get("on"):
        return False
    days = (cfg.get("days") or []) if kind == "morning" else [cfg.get("day", 0)]
    if now.weekday() not in days:
        return False
    at = now.replace(hour=cfg.get("hour", 7), minute=cfg.get("minute", 0), second=0, microsecond=0)
    return at <= now < at + WINDOW[kind]


# ── "weekdays at 7", "Mon–Sat 6:30am", "no" — the owner's own words ───────────────────────

_DAY_RE = {i: re.compile(rf"\b{n[:3]}\w*\b", re.I) for i, n in
           enumerate(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))}
_RANGE_RE = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)\w*\s*(?:-|–|to|till|until)\s*(mon|tue|wed|thu|fri|sat|sun)\w*", re.I)
_TIME_RE = re.compile(r"\b(\d{1,2})(?:[:h.](\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?(?![\d%/])", re.I)
_NO_RE = re.compile(r"^\s*(?:no|nope|nah|not now|no thanks|no thank you|none|skip|off|stop)\b", re.I)
_YES_RE = re.compile(r"^\s*(?:yes|yep|yeah|ja|sure|ok|okay|please|go ahead)\b", re.I)
_ABBR = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def parse_time(text: str) -> Optional[tuple]:
    for m in _TIME_RE.finditer(text or ""):
        h, mins, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").lower().replace(".", "")
        if ampm == "pm" and h < 12:
            h += 12
        elif ampm == "am" and h == 12:
            h = 0
        if 0 <= h <= 23 and 0 <= mins <= 59:
            return h, mins
    return None


def parse_days(text: str) -> Optional[List[int]]:
    t = (text or "").lower()
    if re.search(r"\b(?:every ?day|daily|7 days|seven days|all week)\b", t):
        return list(range(7))
    if re.search(r"\b(?:week ?days|work ?days|working days|mon(?:day)?\s*(?:-|–|to)\s*fri(?:day)?)\b", t):
        return [0, 1, 2, 3, 4]
    r = _RANGE_RE.search(t)
    if r:
        a, b = _ABBR.index(r.group(1)[:3]), _ABBR.index(r.group(2)[:3])
        return list(range(a, b + 1)) if a <= b else sorted(set(range(a, 7)) | set(range(0, b + 1)))
    days = sorted(i for i, rx in _DAY_RE.items() if rx.search(t))
    return days or None


def parse_schedule(kind: str, text: str) -> Optional[Dict[str, Any]]:
    """The owner's reply as settings, or None when it can't be read. "no" → off; "yes" → the
    default time; otherwise the time and days given (a missing part keeps the default)."""
    text = (text or "").strip()
    if _NO_RE.match(text):
        return {"on": False}
    when, days = parse_time(text), parse_days(text)
    if not when and not days:
        return {"on": True} if _YES_RE.match(text) else None
    out: Dict[str, Any] = {"on": True}
    if when:
        if not 5 <= when[0] <= 21:
            return None
        out["hour"], out["minute"] = when
    if days:
        if kind == "morning":
            out["days"] = days
        else:
            out["day"] = days[0]
    return out


def ask_text(kind: str) -> str:
    if kind == "weekly":
        return ("Which day and time should the weekly advisor come? e.g. *Monday at 8* or *Friday 3pm*.")
    return ("What time should the morning brief come, and on which days? e.g. *weekdays at 7*, "
            "*Mon–Sat 6:30*, or *every day at 8*.")
