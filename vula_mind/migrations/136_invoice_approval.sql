-- 136_invoice_approval.sql — opt-in client approval via a public link.
--
-- Deliberately an ORTHOGONAL set of fields, not a new status value — keeps the status CHECK
-- constraint (just fixed once this session for part_paid) and every existing status-driven
-- feature (aging buckets, overdue cadence, dashboard filters) completely untouched. Approval is
-- a fact ABOUT an invoice, displayed alongside its status, never a replacement lifecycle stage —
-- confirmed informational-only, never gates Mark paid/Record payment.
alter table commerce_invoices add column if not exists requires_approval boolean not null default false;
alter table commerce_invoices add column if not exists approval_token text;
alter table commerce_invoices add column if not exists approved_at timestamptz;
alter table commerce_invoices add column if not exists approved_by text;
