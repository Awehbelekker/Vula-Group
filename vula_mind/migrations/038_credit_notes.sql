-- 038_credit_notes.sql — credit notes reuse the invoice engine (doc_type='credit_note').
alter table commerce_invoices drop constraint if exists commerce_invoices_doc_type_chk;
alter table commerce_invoices
    add constraint commerce_invoices_doc_type_chk
    check (doc_type in ('invoice','quote','proforma','credit_note'));
alter table commerce_invoices add column if not exists credited_invoice_id uuid;  -- the invoice being credited
