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
    if not (settings.platform_wa_phone_id and settings.platform_wa_token and settings.platform_waba_id):
        gaps.append(_gap("Vula's own WhatsApp number is not set: operator alerts are only logged",
                         "Set PLATFORM_WA_PHONE_ID, PLATFORM_WABA_ID and PLATFORM_WA_TOKEN"))
    if _is_staging() and settings.public_base_url.rstrip("/") == _PROD_BASE_URL.rstrip("/"):
        gaps.append(_gap("VULA_ENV is staging but PUBLIC_BASE_URL is still production's: pay links, "
                         "receipts and OAuth/webhook callbacks would point at production",
                         "Set PUBLIC_BASE_URL to the staging service's own URL"))
    for m in schema_missing or []:
        gaps.append(_gap(f"Database is missing {m}",
                         "Run tools/migrate.py apply (or apply that migrations/*.sql in the Supabase SQL editor)"))
    return gaps


# config.py's default PUBLIC_BASE_URL is the production Railway service.
_PROD_BASE_URL = type(settings).model_fields["public_base_url"].default


def _is_staging() -> bool:
    return (settings.vula_env or "").strip().lower() == "staging"


def _setting(what: str, state: str, note: str) -> dict:
    return {"what": what, "state": state, "note": note}


def _group(what: str, names: dict[str, str], off_note: str) -> dict:
    """'set' / 'partly set' / 'off by design' for a group of env vars. Only NAMES are reported,
    never values (these are credentials)."""
    missing = [env for env, val in names.items() if not (val or "").strip()]
    if not missing:
        return _setting(what, "set", "All of " + ", ".join(names) + " are set")
    if len(missing) == len(names):
        return _setting(what, "off by design", off_note)
    return _setting(what, "partly set", "Not set: " + ", ".join(missing))


def optional_settings() -> list[dict]:
    """Platform settings that may be off on purpose. Informational: none of these makes /ready
    fail (the one that can — staging on production's PUBLIC_BASE_URL — is in platform_gaps)."""
    s = settings
    kind = "production default" if s.public_base_url.rstrip("/") == _PROD_BASE_URL.rstrip("/") else "custom"
    out = [_setting("PUBLIC_BASE_URL", "set",
                    f"{s.public_base_url} ({kind}; VULA_ENV={s.vula_env or 'production'})")]
    if s.resend_api_key:
        note = f"Sending as {s.from_email or '(FROM_EMAIL not set)'}"
        if not s.team_email:
            note += "; TEAM_EMAIL not set, so team notifications and the weekly digest go nowhere"
        out.append(_setting("Email (Resend)", "set", note))
    else:
        out.append(_setting("Email (Resend)", "off by design",
                            "RESEND_API_KEY not set: no email goes out (FROM_EMAIL, TEAM_EMAIL unused)"))
    out.append(_group("PayFast (platform account)",
                      {"PAYFAST_MERCHANT_ID": s.payfast_merchant_id, "PAYFAST_MERCHANT_KEY": s.payfast_merchant_key,
                       "PAYFAST_PASSPHRASE": s.payfast_passphrase},
                      "Vula's own PayFast account (signup subscription payments) is off; tenants connect their own under Settings › Payments"))
    out.append(_group("Yoco (platform keys)",
                      {"YOCO_SECRET_KEY": s.yoco_secret_key, "YOCO_PUBLIC_KEY": s.yoco_public_key,
                       "YOCO_WEBHOOK_SECRET": s.yoco_webhook_secret},
                      "No platform Yoco keys; tenants connect their own Yoco account"))
    return out


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
    # Invoices: a VAT vendor's tax invoice needs its VAT number and address (VAT Act s20(4));
    # without the number Vula prints "Invoice" instead (vula/commerce/pdf.py::is_tax_invoice).
    for r in _rows(client, "commerce_invoice_settings", "tenant_id,vat_registered,vat_number,registered_address"):
        tid = r.get("tenant_id")
        if tid not in tids or r.get("vat_registered") is False:
            continue
        missing = [w for w, v in (("VAT number", r.get("vat_number")), ("registered address", r.get("registered_address")))
                   if not (v or "").strip()]
        if missing:
            add(tid, f"Marked VAT-registered but its {' and '.join(missing)} "
                     f"{'are' if len(missing) > 1 else 'is'} missing, so invoices "
                     f"can't be valid tax invoices",
                "Add them under Settings › Invoices, or untick VAT registered if it isn't")
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
        "settings": optional_settings(),
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


_STATE_KEY = "readiness_last"


def is_new(line: str) -> bool:
    """True when `line` differs from the last summary sent. Records it in vula_platform_state
    (migration 234), so a redeploy with the same gaps doesn't alert again: the service has no
    volume, so a file on disk was wiped by every deploy and the same list went out each time.
    Falls back to the file when the table isn't there."""
    import hashlib
    digest = hashlib.sha256(line.encode()).hexdigest()
    try:
        from vula.commerce import service
        db = service._client()
        rows = (db.table("vula_platform_state").select("value").eq("key", _STATE_KEY)
                .limit(1).execute().data or [])
        if rows and (rows[0].get("value") or "") == digest:
            return False
        db.table("vula_platform_state").upsert(
            {"key": _STATE_KEY, "value": digest, "updated_at": datetime.now(timezone.utc).isoformat()},
            on_conflict="key").execute()
        return True
    except Exception as exc:  # noqa: BLE001 — no DB or no migration 234: the file below
        logger.debug("readiness: platform state unavailable (%s)", type(exc).__name__)
    path = settings.data_dir / "readiness_last.txt"
    try:
        if path.exists() and path.read_text().strip() == digest:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(digest)
    except OSError as exc:
        logger.info("readiness: couldn't record the last summary (%s)", type(exc).__name__)
    return True
