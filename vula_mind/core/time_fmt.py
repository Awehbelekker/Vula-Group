"""core/time_fmt.py — shared relative-age labelling for conversation history lines.

Both vula/commerce/service.py (commerce sessions) and vula/chat/history.py (knowledge-mode
chat) inject prior turns into the model's prompt with zero timing signal — confirmed live,
2026-08-27, gerflor: a 7-hour-old unrelated message ("I've set a reminder to follow up with
Tersia...") was echoed back almost verbatim as if it were fresh context, because the model had
no way to tell it apart from something said seconds ago. This is the one piece of logic both
paths need identically — everything else about how each formats its transcript stays
independent.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional


def _parse(created_at: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat((created_at or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def relative_age_label(created_at: str, now: Optional[datetime] = None) -> str:
    """'just now' / '12 min ago' / '3 hr ago' / 'yesterday' / '4 days ago'. Never raises —
    unparsable input returns '' so a formatting bug can never break a reply."""
    dt = _parse(created_at)
    if dt is None:
        return ""
    now = now or datetime.now(timezone.utc)
    delta = now - dt
    seconds = delta.total_seconds()
    if seconds < 0:
        return "just now"  # clock skew — never show a future age
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        mins = int(seconds // 60)
        return f"{mins} min ago"
    if seconds < 86400:
        hrs = int(seconds // 3600)
        return f"{hrs} hr ago"
    if delta.days == 1 or (delta.days == 0 and now.date() != dt.date()):
        return "yesterday"
    return f"{delta.days} days ago"


def cutoff_iso(hours: float, now: Optional[datetime] = None) -> str:
    """ISO timestamp `hours` before now, for a `.gte("created_at", ...)` query filter."""
    now = now or datetime.now(timezone.utc)
    return (now - timedelta(hours=hours)).isoformat()
