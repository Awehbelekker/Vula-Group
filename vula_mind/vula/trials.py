"""Free-trial notices (gap audit 5.1).

The trial owner is emailed 7, 3, 1 and 0 days before the end. Email (Resend) is off for launch,
so on 27 Oct none of those were delivered, yet the daily loop counted them as sent and nobody
else was told: a trial ending on 30 Oct would have passed with no word to anyone. The operator
now gets one WhatsApp line a day listing trials ending in the next week or ended in the last
week, unpaid, so someone can follow up by hand.

Nothing here suspends a workspace: what happens at the end of a trial is a business decision.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional, Tuple

EMAIL_DAYS = (7, 3, 1, 0)
WATCH_DAYS = 7


def days_left(row: dict, today: date) -> Optional[int]:
    try:
        return (date.fromisoformat((row.get("trial_ends") or "")[:10]) - today).days
    except ValueError:
        return None


def due_emails(rows: List[dict], today: date) -> List[Tuple[dict, int]]:
    """(tenant row, days left) for each unpaid active trial that gets an email today."""
    out = []
    for r in rows:
        d = days_left(r, today)
        if d in EMAIL_DAYS and not r.get("paid") and r.get("status", "active") == "active":
            out.append((r, d))
    return out


def operator_line(rows: List[dict], today: date) -> Optional[str]:
    """One line for the operator: unpaid trials ending within a week, or ended within a week."""
    items = []
    for r in rows:
        d = days_left(r, today)
        if d is None or r.get("paid") or r.get("status", "active") != "active" or not -WATCH_DAYS <= d <= WATCH_DAYS:
            continue
        name = r.get("company_name") or r.get("tenant_id")
        when = ("ends today" if d == 0 else f"ends in {d} day{'s' if d != 1 else ''}" if d > 0
                else f"ended {-d} day{'s' if d != -1 else ''} ago")
        items.append((d, f"{name} ({r.get('plan') or 'starter'}) {when}"))
    if not items:
        return None
    return "🧾 Trials to follow up (unpaid): " + "; ".join(t for _, t in sorted(items)) + "."
