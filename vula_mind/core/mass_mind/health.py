"""
core/mass_mind/health.py — systemic health watch (Mass Mind Phase 1, migration 160).

vula/api/server.py's _stale_escalation_scheduler_loop and _stale_handoff_scheduler_loop each
recover ONE tenant's stuck conversation independently, with no visibility into whether the SAME
thing is happening across many tenants at once — the signal that says a skill or tool is broken
platform-wide, not that one tenant got unlucky. record_event() is called by both loops at the
moment they recover something; systemic_incidents() is the rollup that turns "N isolated
recoveries" into "this is happening to M distinct tenants right now" — the actual test for a
real incident versus ordinary background noise (some rate of stale escalations/handoffs is
completely normal; a handful of tenants all hitting the same one, at once, is not).

Only tenant_id + a fixed event `kind` + a small structured `detail` cross into this store — no
raw question text, no customer names, no conversation content. Same POPIA-safe discipline as
core/memory/reflection.py and the rest of core/reasoning_telemetry.py.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# What a caller is allowed to record — a fixed, reviewed vocabulary, not an arbitrary string,
# so a typo'd kind can't silently create a permanent blind spot in the rollup.
KINDS = ("stale_escalation_abandoned", "stale_handoff_auto_resumed")

# In-process de-dup so a real, ongoing incident pings the platform operator once per cooldown,
# not once per _mass_mind_health_watch_loop tick (server.py, hourly) for as long as it lasts.
# Module-level and best-effort on purpose: a leadership flap resetting this (see
# scheduler-leadership-task-leak) means at most one duplicate alert, never a missed one, and
# this is a low-frequency human notification, not a correctness-critical path.
_ALERT_COOLDOWN_SECONDS = 12 * 3600
_last_alerted: Dict[str, float] = {}


def _client():
    from vula.commerce import service
    return service._client()


def record_event(tenant_id: str, kind: str, detail: Optional[Dict[str, Any]] = None) -> None:
    """Record one recovery firing. Fails open, logged at debug — a health-signal write must
    never break the recovery it's describing."""
    if kind not in KINDS:
        logger.debug("record_event: unknown kind %r, dropped", kind)
        return
    try:
        _client().table("vula_health_events").insert({
            "tenant_id": tenant_id, "kind": kind, "detail": detail or {},
        }).execute()
    except Exception as exc:
        logger.debug("health event write skipped (run migration 160?): %s", exc)


def systemic_incidents(hours: float = 24.0, min_tenants: int = 3) -> List[Dict[str, Any]]:
    """Which recorded kinds fired for at least `min_tenants` DISTINCT tenants in the last
    `hours` — never raises, returns [] on any failure (including migration 160 not yet
    applied) so a caller can call this unconditionally on a schedule."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    try:
        rows = (_client().table("vula_health_events").select("tenant_id,kind")
                .gte("created_at", cutoff).limit(5000).execute().data or [])
    except Exception as exc:
        logger.debug("systemic incident scan skipped (run migration 160?): %s", exc)
        return []

    tenants_by_kind: Dict[str, set] = {}
    counts: Dict[str, int] = {}
    for r in rows:
        k = r.get("kind")
        if not k:
            continue
        tenants_by_kind.setdefault(k, set()).add(r.get("tenant_id"))
        counts[k] = counts.get(k, 0) + 1

    return [
        {"kind": k, "tenant_ids": sorted(t for t in tenants if t),
         "tenant_count": len(tenants), "event_count": counts[k], "window_hours": hours}
        for k, tenants in tenants_by_kind.items()
        if len(tenants) >= min_tenants
    ]


def should_alert(kind: str, now: Optional[float] = None) -> bool:
    """True at most once per _ALERT_COOLDOWN_SECONDS per kind — call this right before sending
    the operator alert, and only actually send if it returns True."""
    now = now if now is not None else time.time()
    last = _last_alerted.get(kind)
    if last is not None and (now - last) < _ALERT_COOLDOWN_SECONDS:
        return False
    _last_alerted[kind] = now
    return True


_DESCRIPTIONS = {
    "stale_escalation_abandoned": "customer questions going unanswered long enough that Vula "
                                  "apologised on the team's behalf",
    "stale_handoff_auto_resumed": "WhatsApp handoffs sitting paused long enough that Vula "
                                  "auto-resumed replying",
}


def describe(incident: Dict[str, Any]) -> str:
    """Human sentence for the operator alert — kept out of server.py so the wording lives next
    to the data it describes."""
    what = _DESCRIPTIONS.get(incident["kind"], incident["kind"])
    return (f"🧠 Mass Mind: {what} for {incident['tenant_count']} different tenants in the last "
            f"{int(incident['window_hours'])}h ({incident['event_count']} events total: "
            f"{', '.join(incident['tenant_ids'])}). Same thing happening across several tenants "
            f"at once usually means something's broken platform-wide, not that any one of them "
            f"got unlucky — worth a look.")
