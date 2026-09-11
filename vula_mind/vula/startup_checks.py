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
#
# 2026-09-11: audited every migration 100+ that had no sentinel (pre-go-live brief item —
# 17/157 covered, no evidence the rest had ever been reviewed for this). Added one row per
# migration that adds a table/column a LIVE code path actually reads (verified against real
# call sites, not just "this table exists somewhere"). Deliberately NOT added: 113/114/115
# (RLS-only on already-existing tables — absence isn't a "table doesn't exist" failure, it's a
# security-posture gap covered separately, see [[security-remediation-pass]]), 127 (pure data
# seed, nothing to probe), 133 (a CHECK constraint fix, not a new column), 143/144/145 (index/
# constraint changes on vula_filed_documents — its actual new column, content_hash, is already
# migration 101 below; a missing index doesn't make a `select` fail the way this probe checks).
_SENTINELS: list[tuple[str, str, str | None]] = [
    ("071", "vula_wa_msg_dedup", None),
    ("100", "vula_page_versions", None),
    ("101", "vula_filed_documents", "content_hash"),
    ("102", "commerce_suppliers", "layout_signature"),
    ("103", "commerce_invoice_settings", "footer_text"),
    ("104", "commerce_orders", "attributed_broadcast_id"),
    ("105", "commerce_discount_codes", "first_order_only"),
    ("106", "commerce_email_campaigns", None),
    ("107", "vula_merchant_audit", None),
    ("108", "vula_tenants", "paid"),
    ("109", "vula_filed_documents", "customer_phone"),
    ("110", "commerce_contacts", "created_by"),
    ("111", "vula_escalations", "stale_notified_at"),
    ("112", "vula_team_members", "last_notified_at"),
    ("116", "vula_tenant_config", "persona_prompt"),
    ("117", "vula_reminders", None),
    ("118", "commerce_flows", None),
    ("119", "vula_tenant_config", "persona_prompt_suggested"),
    ("120", "vula_email_voice_samples", None),
    ("121", "journal_entries", None),
    ("122", "commerce_number_counters", None),
    ("123", "commerce_purchase_orders", "sent_channel"),
    ("124", "commerce_orders", "refund_status"),
    ("125", "vula_project_claims", None),
    ("126", "vula_dynamics365_accounts", None),
    ("128", "commerce_invoice_settings", "header_nav_position"),
    ("129", "vula_project_boq", "sections"),
    ("130", "commerce_invoice_payments", None),
    ("131", "commerce_invoices", "reminder_stage"),
    ("132", "commerce_bank_transactions", "proposed_match_type"),
    ("134", "commerce_invoices", "invoiced_cents"),
    ("135", "commerce_invoices", "sent_channel"),
    ("136", "commerce_invoices", "requires_approval"),
    ("137", "commerce_automation_firings", None),
    ("138", "vula_team_members", "call_sheet_recipient_email"),
    ("139", "vula_team_members", "expense_budget_cents"),
    ("140", "commerce_expenses", "purpose_category"),
    ("141", "commerce_expenses", "purpose_detail"),
    ("142", "commerce_expenses", "odometer_km"),
    ("146", "commerce_pending_confirmations", None),
    ("147", "vula_tenant_config", "meta_catalog_id"),
    ("148", "vula_voice_retry_queue", None),
    ("149", "vula_escalations", "customer_notified_at"),
    ("150", "vula_learned_answers", "status"),
    ("151", "vula_clickup_accounts", "webhook_secret"),
    ("152", "vula_business_rules", None),
    ("153", "vula_wa_outbound", None),
    ("154", "commerce_merchant_profiles", None),
    ("155", "commerce_bank_transactions", "asked_at"),
    ("156", "vula_stock_sheets", None),
    ("157", "vula_media_dedup", None),
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
