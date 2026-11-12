-- 260_created_by.sql — who made each quote, invoice and order (12 Nov 2026).
--
-- So sales can be counted per person ("what did Thabo do this week?", Team › Activity, custom
-- sheets). Set from the request (vula/acting.py): the WhatsApp sender's number, or the signed-in
-- dashboard member's number (else their email). Empty for a customer's own checkout and for
-- rows from before this. A converted quote's invoice keeps the quote's person. Idempotent.

alter table commerce_invoices add column if not exists created_by text;
alter table commerce_orders add column if not exists created_by text;
create index if not exists idx_commerce_invoices_created_by on commerce_invoices (tenant_id, created_by, issue_date);
create index if not exists idx_commerce_orders_created_by on commerce_orders (tenant_id, created_by, created_at);
