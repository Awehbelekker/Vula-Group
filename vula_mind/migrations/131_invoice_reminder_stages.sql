-- 131_invoice_reminder_stages.sql — multi-stage overdue-invoice reminder cadence.
--
-- _process_overdue_invoices used to be a single one-shot reminder the moment an invoice crossed
-- its due date, with no pre-due nudge and no escalation for invoices that stayed unpaid. Rather
-- than trying to force this into job_config.py's registry (confirmed: one on/off timer per job
-- type, no concept of multiple named stages per job — not a clean fit), this generalizes the
-- exact idempotency pattern unpaid_order_chase already uses (a claim column + conditional
-- update, `commerce_orders.followup_sent_at`) from a boolean to an ordered stage, so each of
-- pre_due/due/firm/escalated fires exactly once per invoice, race-safe, on the SAME existing
-- daily job — no new scheduling infrastructure.
alter table commerce_invoices add column if not exists reminder_stage text;         -- null | pre_due | due | firm | escalated
alter table commerce_invoices add column if not exists last_reminded_at timestamptz;
