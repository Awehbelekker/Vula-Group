"""What still has to be set up before each switched-on feature can work (gap audit 1.1).

config.warn_missing() and vula/startup_checks.py each log a line at boot, but nothing gathered
them, and nothing looked at what a tenant had switched ON: Tap to Pay in testing with no PayFast
account connected only showed up when a customer tapped. report() collects every gap, platform
and per tenant, with the fix for each. Served at GET /ready (API key) and summarised to the
operator once after boot (vula/api/server.py).

Read-only, best-effort: a check that can't run is reported as such, never raised.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from config import settings

logger = logging.getLogger(__name__)


def _gap(what: str, fix: str) -> dict:
    return {"what": what, "fix": fix}


def platform_gaps(schema_missing: Optional[list] = None) -> list[dict]:
    gaps = []
    if not settings.api_key:
        gaps.append(_gap("API_KEY is not set: the API is unauthenticated", "Set API_KEY in Railway"))
    if not settings.supabase_url or not settings.supabase_service_key:
        gaps.append(_gap("Supabase is not configured", "Set SUPABASE_URL and SUPABASE_SERVICE_KEY"))
    if not settings.whatsapp_token:
        gaps.append(_gap("WHATSAPP_TOKEN is not set: no WhatsApp replies", "Set WHATSAPP_TOKEN"))
    if not settings.openrouter_api_key:
        gaps.append(_gap("No cloud AI key: when the local model is down nothing answers",
                         "Set OPENROUTER_API_KEY"))
    if not settings.enforce_tenant_auth:
        gaps.append(_gap("Tenant admin routes are not access-checked", "Set ENFORCE_TENANT_AUTH=true"))
    if not settings.platform_support_phone:
        gaps.append(_gap("Operator alerts have nowhere to go", "Set PLATFORM_SUPPORT_PHONE"))
    for m in schema_missing or []:
        gaps.append(_gap(f"Database is missing {m}", "Apply that migrations/*.sql in the Supabase SQL editor"))
    return gaps


def _rows(client, table: str, cols: str, **eq) -> list[dict]:
    q = client.table(table).select(cols)
    for k, v in eq.items():
        q = q.eq(k, v)
    return q.execute().data or []


def tenant_gaps(client) -> dict[str, list[dict]]:
    """Gaps per active tenant, for the features that tenant has switched on."""
    from vula.api import tenants as _t
    from vula.startup_checks import unready_payment_tenants

    out: dict[str, list[dict]] = {}

    def add(tid: str, what: str, fix: str) -> None:
        out.setdefault(tid, []).append(_gap(what, fix))

    tids = sorted({r["tenant_id"] for r in _rows(client, "vula_tenant_config", "tenant_id")
                   if r.get("tenant_id") and _t.is_active(r["tenant_id"])})
    wa = {r["tenant_id"] for r in _rows(client, "vula_whatsapp_accounts", "tenant_id,status")
          if r.get("status") == "connected"}
    for tid in unready_payment_tenants():
        add(tid, "No way to take payment (no card gateway, no EFT details)",
            "Connect one under Settings › Payments")

    # Tap to Pay, for tenants that switched it on (or the operator override list)
    try:
        from vula.tap.api import _override_tenants
        tap = {r["tenant_id"]: r.get("mode") for r in _rows(client, "kb_settings", "tenant_id,mode")
               if r.get("mode") in ("testing", "live")}
        for tid in _override_tenants():
            tap[tid] = "live"
    except Exception as exc:  # noqa: BLE001
        logger.info("readiness: Tap to Pay check skipped (%s)", type(exc).__name__)
        tap = {}
    payfast = {r["tenant_id"]: r for r in _rows(client, "vula_payment_providers", "tenant_id,mode,active",
                                                provider="payfast")}
    for tid, mode in sorted(tap.items()):
        pf = payfast.get(tid)
        if not pf or pf.get("active") is False:
            add(tid, f"Tap to Pay is {mode} but no PayFast account is connected",
                "Connect PayFast under Settings › Payments, or switch Tap to Pay off")
        elif mode == "live" and pf.get("mode") == "test":
            add(tid, "Tap to Pay is live but PayFast is in sandbox mode", "Switch PayFast to live")
        if tid not in wa:
            add(tid, f"Tap to Pay is {mode} but the tenant has no connected WhatsApp number",
                "Connect a number under Settings › WhatsApp")
        if mode == "live" and not settings.tap_reminder_template:
            add(tid, "Unpaid-bill reminders can't go out after 24 hours",
                "Get the reminder template approved by Meta, then set TAP_REMINDER_TEMPLATE")
        if not (settings.vapid_public_key and settings.vapid_private_key):
            add(tid, "The coach app gets no push notifications",
                "Generate keys (scripts/gen_vapid.py), set VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY")
        if mode == "live" and not settings.tap_hash_pepper:
            add(tid, "Tap to Pay hashes are keyed off the Supabase key (they change if it's rotated)",
                "Set TAP_HASH_PEPPER before going live")
    for tid in tids:
        out.setdefault(tid, [])
    return out


def report() -> dict:
    """Every gap, platform and per tenant. `ready` is False only for platform gaps: one tenant's
    unfinished setup doesn't make the service unready for the others."""
    from vula import startup_checks
    missing = startup_checks.LAST_MISSING
    if missing is None:
        missing = startup_checks.check_schema()
    platform = platform_gaps(missing)
    try:
        from vula.commerce import service
        tenants = tenant_gaps(service._client())
    except Exception as exc:  # noqa: BLE001
        tenants = {}
        platform.append(_gap(f"Per-tenant checks couldn't run ({type(exc).__name__})",
                             "Check the Supabase connection"))
    return {
        "ready": not platform,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": platform,
        "tenants": {t: g for t, g in tenants.items() if g},
        "tenants_ready": sorted(t for t, g in tenants.items() if not g),
    }


def summary(rep: dict) -> Optional[str]:
    """One operator line, or None when nothing is missing."""
    parts = [g["what"] for g in rep["platform"]]
    parts += [f"{t}: {g['what']}" for t, gs in rep["tenants"].items() for g in gs]
    if not parts:
        return None
    shown = "; ".join(parts[:4]) + (f"; and {len(parts) - 4} more" if len(parts) > 4 else "")
    return f"🔧 Vula setup: {len(parts)} thing(s) still to do. {shown}. Full list: GET /ready"


def is_new(line: str) -> bool:
    """True when `line` differs from the last summary sent (kept on the data volume, so a
    redeploy with the same gaps doesn't alert again). Records it."""
    import hashlib
    path = settings.data_dir / "readiness_last.txt"
    digest = hashlib.sha256(line.encode()).hexdigest()
    try:
        if path.exists() and path.read_text().strip() == digest:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(digest)
    except OSError as exc:
        logger.info("readiness: couldn't record the last summary (%s)", type(exc).__name__)
    return True
