-- 135_invoice_send_channel.sql — record HOW an invoice was actually sent, not just that it was.
-- Mirrors commerce_purchase_orders.sent_channel (migration 123) — same pattern, applied here.
alter table commerce_invoices add column if not exists sent_channel text;  -- 'email' | 'whatsapp' | 'manual'
alter table commerce_invoices add column if not exists sent_at timestamptz;
