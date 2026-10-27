"""Free-trial notices (gap audit 5.1).

The trial owner is emailed 7, 3, 1 and 0 days before the end. Email (Resend) is off for launch,
so on 27 Oct none of those were delivered, yet the daily loop counted them as sent and nobody
else was told: a trial ending on 30 Oct would have passed with no word to anyone. The operator
now gets one WhatsApp line a day listing trials ending in the next week or ended in the last
week, unpaid, so someone can follow up by hand.

Nothing here suspends a workspace: what happens at the end of a trial is a business decision.

27 Oct (Ian): the trial is TRIAL_DAYS (14) and runs from when the business's WhatsApp number goes
live, not from signup: Meta's number approval can take days and nothing works before it. Signup
sets the end date; connecting a number pushes it to 14 days from then (never earlier).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple

from config import settings

logger = logging.getLogger(__name__)

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


def start_on_go_live(tenant_id: str, db=None, now: Optional[datetime] = None) -> Optional[str]:
    """A trial business's WhatsApp number just went live: its trial now ends trial_days from
    today, if that's later than the end it has. Returns the new end date, or None (paid, no
    trial, or nothing to change). Best-effort: never raises into the connect flow."""
    try:
        if db is None:
            from vula.commerce import service
            db = service._client()
        rows = (db.table("vula_tenants").select("trial_ends,paid").eq("tenant_id", tenant_id)
                .limit(1).execute().data or [])
        if not rows or rows[0].get("paid") or not rows[0].get("trial_ends"):
            return None
        current = datetime.fromisoformat(str(rows[0]["trial_ends"]).replace("Z", "+00:00"))
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        new = (now or datetime.now(timezone.utc)) + timedelta(days=settings.trial_days)
        if new <= current:
            return None
        db.table("vula_tenants").update({"trial_ends": new.isoformat()}).eq("tenant_id", tenant_id).execute()
        logger.info("trial for %s now runs to %s (WhatsApp number live)", tenant_id, new.date())
        return new.date().isoformat()
    except Exception as exc:  # noqa: BLE001
        logger.warning("trial start on go-live skipped for %s: %s", tenant_id, type(exc).__name__)
        return None
