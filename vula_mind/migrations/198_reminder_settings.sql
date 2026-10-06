-- 198: per-tenant payment-reminder settings (2026-10-06, finance back-office brief, capability 3).
-- reminder_mode decides what the daily overdue job does with a customer reminder that is due:
--   'propose' (default) — the owner gets one WhatsApp list with Confirm/Cancel; nothing goes to a
--                         customer until they tap Confirm (the propose-confirm gate);
--   'auto'              — sent straight away (the behaviour before this migration);
--   'off'               — never sent to customers (the team's escalation alert still fires).
-- reminder_tone: 'friendly' | 'firm' — the wording of the reminder.
-- commerce_invoices.reminder_proposed_stage: the stage already put to the owner, so the same
-- reminder isn't proposed again every morning. Both tables already have RLS on. Idempotent.

alter table commerce_invoice_settings add column if not exists reminder_mode text default 'propose';
alter table commerce_invoice_settings add column if not exists reminder_tone text default 'friendly';
alter table commerce_invoices add column if not exists reminder_proposed_stage text;
