-- 037_invoice_pay_link.sql — store a Yoco "Pay now" link per invoice.
alter table commerce_invoices add column if not exists pay_url          text;
alter table commerce_invoices add column if not exists yoco_checkout_id text;
