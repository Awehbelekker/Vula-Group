"""vula/startup_checks.py — boot-time "did the migrations actually get applied" probe.

Migrations in migrations/*.sql are applied BY HAND in the Supabase SQL editor — there is no
runner and no schema_migrations table. The recurring failure mode (whatsapp_chat_messages,
vula_chat_messages, and others) is: code ships expecting a table/column that was never created
in prod, and the first sign of it is a silent runtime exception deep in a request.

This turns that into one loud line at boot. It is a curated sentinel list, not exhaustive —
add a row when a migration lands that a live path depends on. Best-effort: any error here
must never stop the server coming up.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("vula.startup")

# (migration, table, column-or-None). column=None just checks the table exists. Names verified
# against migrations/*.sql — keep them accurate or this emits false alarms.
_SENTINELS: list[tuple[str, str, str | None]] = [
    ("112", "vula_team_members", "last_notified_at"),
    ("119", "vula_tenant_config", "persona_prompt_suggested"),
    ("121", "journal_entries", None),
    ("123", "commerce_purchase_orders", "sent_channel"),
    ("125", "vula_project_claims", None),
    ("128", "commerce_invoice_settings", "header_nav_position"),
    ("148", "vula_voice_retry_queue", None),
    ("149", "vula_escalations", "customer_notified_at"),
    ("150", "vula_learned_answers", "status"),
    ("151", "vula_clickup_accounts", "webhook_secret"),
    ("152", "vula_business_rules", None),
    ("153", "vula_wa_outbound", None),
    ("154", "commerce_merchant_profiles", None),
    ("155", "commerce_bank_transactions", "asked_at"),
    ("156", "vula_stock_sheets", None),
]


def check_schema() -> list[str]:
    """Probe each sentinel. Returns a list of human-readable "missing" strings (also logged)."""
    try:
        from vula.commerce import service
        client = service._client()
    except Exception as exc:  # noqa: BLE001 — no DB client, nothing to check
        logger.info("schema check skipped — no Supabase client (%s)", exc)
        return []

    missing: list[str] = []
    for migration, table, column in _SENTINELS:
        sel = column or "*"
        try:
            client.table(table).select(sel).limit(1).execute()
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "does not exist" in msg or "could not find" in msg or "undefined" in msg:
                what = f"{table}.{column}" if column else table
                missing.append(f"migration {migration} ({what})")
            else:
                logger.debug("schema probe inconclusive for %s: %s", table, exc)

    if missing:
        logger.error(
            "SCHEMA DRIFT — these migrations appear UNAPPLIED in this database: %s. "
            "Apply the matching migrations/*.sql in the Supabase SQL editor.",
            "; ".join(missing),
        )
    else:
        logger.info("schema check OK — all %d migration sentinels present", len(_SENTINELS))
    return missing
