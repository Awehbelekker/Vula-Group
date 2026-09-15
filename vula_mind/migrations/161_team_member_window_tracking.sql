-- 161_team_member_window_tracking.sql (renumbered TWICE now: originally 108, moved to 112 after
-- colliding with 108_tenants_paid_column.sql, then moved again to 161 — 2026-09-15 migration
-- audit found it had landed back on a second collision, this time with
-- 112_rls_credential_tables.sql (part of the documented 112-115 security-remediation block —
-- see [[security-remediation-pass]] — so that one keeps its number and this one moves again).
-- Already run against prod under both earlier filenames; this is a bookkeeping rename only, not
-- a re-run. See tools/check_migrations_rls.py's duplicate-number guard, added the same day so a
-- third collision fails CI instead of sitting unnoticed again.) —
-- track each team member's WhatsApp 24h conversation
-- window so a "keep it open" nudge can fire before it closes, instead of every proactive
-- follow-up hitting the template wall. Generalized (any team member, any tenant) — see
-- vula/api/whatsapp.py's inbound dispatch (stamps last_message_at) and _send_wa_template
-- (stamps last_notified_at), plus the new nudge check in the automations-style poller.

alter table vula_team_members
    add column if not exists last_message_at  timestamptz,  -- last inbound WhatsApp FROM this member
    add column if not exists last_notified_at timestamptz;  -- last proactive template send TO this member
