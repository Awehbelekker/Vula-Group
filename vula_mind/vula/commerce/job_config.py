"""
vula/commerce/job_config.py — per-tenant config for the system WhatsApp scheduled jobs.

The scheduler (vula/api/server.py) and the admin endpoints (vula/api/commerce.py) both need the
job registry + config resolution, so it lives here to avoid a server↔commerce circular import.

Five of these jobs are commerce-specific and only ever run for tenants with the "orders" module
(_commerce_jobs_scheduler_loop's tenant list). "stale_escalation_nudge" is the one exception —
server.py's _stale_escalation_scheduler_loop runs it for every tenant, reusing this same
config/interval-claim machinery purely for admin visibility and per-tenant enable/disable.

A tenant with no row (or NULL fields) gets the built-in defaults below — identical to the
behaviour that was hardcoded in server.py before migration 069, so nothing changes for a tenant
until an admin actually edits a job in the ⏰ Scheduling tab.

All times are SAST (UTC+2, no DST) — every current tenant is South African; revisit if that
ever changes.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# job_type → defaults. "kind" drives both the scheduler check and which fields the UI shows:
#   daily    = fires once per SAST day at hour:minute
#   weekly   = fires once per week on day_of_week (0=Mon..6=Sun) at hour:minute
#   interval = fires every interval_minutes
JOB_TYPES: Dict[str, Dict[str, Any]] = {
    "delivery_briefing": {
        "label": "Morning delivery briefing", "kind": "daily",
        "hour": 6, "minute": 30, "template_suffix": "delivery_briefing",
        "description": "Day's order count + paid/unpaid split, sent to the team each morning."},
    "low_stock_alert": {
        "label": "Low-stock alert", "kind": "daily",
        "hour": 7, "minute": 0, "template_suffix": "low_stock_alert",
        "description": "Alerts the team when products drop below 5 in stock (skipped if none)."},
    "sales_summary": {
        "label": "End-of-day sales summary", "kind": "daily",
        "hour": 18, "minute": 0, "template_suffix": "sales_summary",
        "description": "Orders, paid count and revenue for the day, sent to the team."},
    "friday_catch_reminder": {
        "label": "Weekly specials reminder", "kind": "weekly",
        "hour": 8, "minute": 0, "day_of_week": 4, "template_suffix": "friday_catch_reminder",
        "description": "Reminds the team to update the week's specials (default: Fridays)."},
    "unpaid_order_chase": {
        "label": "Unpaid-order customer chase", "kind": "interval",
        "interval_minutes": 30, "template_suffix": "unpaid_order_chase",
        "description": "Nudges customers whose orders sit unpaid 2–4h (once per order)."},
    # The only job here NOT commerce-specific — runs for every tenant (server.py's
    # _stale_escalation_scheduler_loop iterates all tenants, not just ones with the "orders"
    # module), reusing this same config/interval-claim machinery for admin visibility and
    # per-tenant enable/disable via the Scheduling tab. template_suffix is unused (this job
    # sends a plain reply to the assigned helper, not a Meta template message) but kept for
    # structural consistency with the other entries.
    "stale_escalation_nudge": {
        "label": "Stale question reminder", "kind": "interval",
        "interval_minutes": 60, "template_suffix": "stale_escalation_nudge",
        "description": "Reminds the assigned helper about a customer question that's sat "
                       "unanswered a while (every tenant, not just shops)."},
}

SAST = timezone(timedelta(hours=2))


def _client():
    from vula.commerce import service as cs
    return cs._client()


def effective_config(tenant_id: str, job_type: str, row: Optional[dict]) -> Dict[str, Any]:
    """Merge a tenant's stored row (may be None) over the job's built-in defaults."""
    d = JOB_TYPES[job_type]
    row = row or {}
    cfg = {
        "job_type": job_type, "label": d["label"], "kind": d["kind"],
        "description": d["description"],
        "enabled": row.get("enabled", True),
        "template_name": row.get("template_name"),  # None = default prefixed name
        "default_template_suffix": d["template_suffix"],
        "last_fired_at": row.get("last_fired_at"),
    }
    if d["kind"] in ("daily", "weekly"):
        cfg["hour"] = row.get("hour") if row.get("hour") is not None else d["hour"]
        cfg["minute"] = row.get("minute") if row.get("minute") is not None else d["minute"]
    if d["kind"] == "weekly":
        cfg["day_of_week"] = (row.get("day_of_week") if row.get("day_of_week") is not None
                              else d["day_of_week"])
    if d["kind"] == "interval":
        cfg["interval_minutes"] = (row.get("interval_minutes")
                                   if row.get("interval_minutes") else d["interval_minutes"])
    return cfg


def get_configs(tenant_id: str) -> Dict[str, Dict[str, Any]]:
    """All 5 jobs' effective config for a tenant (defaults where unset)."""
    rows: Dict[str, dict] = {}
    try:
        data = (_client().table("commerce_scheduled_job_config").select("*")
                .eq("tenant_id", tenant_id).execute().data or [])
        rows = {r["job_type"]: r for r in data}
    except Exception as exc:
        log.debug("job config read skipped (run migration 069?): %s", exc)
    return {jt: effective_config(tenant_id, jt, rows.get(jt)) for jt in JOB_TYPES}


def upsert_config(tenant_id: str, job_type: str, patch: dict) -> Dict[str, Any]:
    """Store an override for one job. Only whitelisted fields; None clears back to default."""
    if job_type not in JOB_TYPES:
        return {"error": f"unknown job_type '{job_type}'"}
    allowed = {"enabled", "template_name", "hour", "minute", "day_of_week", "interval_minutes"}
    row = {k: patch[k] for k in allowed if k in patch}
    row.update({"tenant_id": tenant_id, "job_type": job_type,
                "updated_at": datetime.now(timezone.utc).isoformat()})
    try:
        res = (_client().table("commerce_scheduled_job_config")
               .upsert(row, on_conflict="tenant_id,job_type").execute())
        stored = res.data[0] if res.data else row
        return effective_config(tenant_id, job_type, stored)
    except Exception as exc:
        return {"error": f"save failed (run migration 069?): {exc}"}


def claim_run(tenant_id: str, job_type: str, cfg: Dict[str, Any]) -> bool:
    """Atomically decide whether this job is due NOW for this tenant, and claim the run by
    stamping last_fired_at. Returns True only for the caller that wins the claim — same
    compare-and-swap pattern as the unpaid-chase per-order fix and the escalation-answer fix
    (both confirmed live 2026-07-16), so a leader handover mid-window can't double-send.
    A tenant with no config row gets one inserted as part of the claim (the unique constraint
    makes the insert itself the race arbiter)."""
    now_utc = datetime.now(timezone.utc)
    now = now_utc.astimezone(SAST)
    kind = cfg["kind"]

    if kind in ("daily", "weekly"):
        if kind == "weekly" and now.weekday() != cfg["day_of_week"]:
            return False
        if (now.hour, now.minute) < (cfg["hour"], cfg["minute"]):
            return False
        last = cfg.get("last_fired_at")
        if last:
            try:
                last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00")).astimezone(SAST)
                if last_dt.date() == now.date():
                    return False  # already fired this SAST day
            except Exception:
                pass
    else:  # interval
        last = cfg.get("last_fired_at")
        if last:
            try:
                last_dt = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
                if (now_utc - last_dt) < timedelta(minutes=cfg["interval_minutes"]):
                    return False
            except Exception:
                pass

    db = _client()
    stamp = now_utc.isoformat()
    try:
        existing = (db.table("commerce_scheduled_job_config").select("id,last_fired_at")
                    .eq("tenant_id", tenant_id).eq("job_type", job_type)
                    .limit(1).execute().data or [])
        if not existing:
            db.table("commerce_scheduled_job_config").insert({
                "tenant_id": tenant_id, "job_type": job_type, "last_fired_at": stamp,
            }).execute()
            return True  # unique constraint errors (→ except) if someone beat us to it
        prev = existing[0].get("last_fired_at")
        q = (db.table("commerce_scheduled_job_config")
             .update({"last_fired_at": stamp}).eq("id", existing[0]["id"]))
        # Conditional on the exact value we read — a concurrent claimant changes it and we lose.
        q = q.eq("last_fired_at", prev) if prev else q.is_("last_fired_at", "null")
        res = q.execute()
        return bool(res.data)
    except Exception as exc:
        log.debug("job claim failed (%s/%s): %s", tenant_id, job_type, exc)
        return False
