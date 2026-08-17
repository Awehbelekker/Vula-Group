-- 134_quote_invoiced_cents.sql — partial (deposit/progress) invoicing from a quote.
--
-- convert_quote_to_invoice used to be strictly one-shot: a quote could only ever be converted
-- ONCE, always for its full total_cents (blocked by converted_invoice_id once set). This tracks
-- how much of a quote's total has actually been invoiced so far, so a quote can be invoiced in
-- portions (e.g. a 30% deposit now, the balance later) until fully covered.
alter table commerce_invoices add column if not exists invoiced_cents bigint not null default 0;
