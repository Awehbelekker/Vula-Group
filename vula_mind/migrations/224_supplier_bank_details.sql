-- 224_supplier_bank_details.sql — a supplier's bank details, kept from their own invoices
-- (25 Oct 2026, Ian: "is Vula allocating details to the supplier when we scan invoices?").
--
-- When an invoice is filed, its checked details (VAT number, phone, email, bank details — each
-- verified to appear on the document, vula/commerce/extraction_quality.verify_identifiers) fill
-- in whatever the supplier's record is missing. Nothing already on the record is overwritten.
-- An invoice whose bank details differ from the ones on file is flagged to the team instead
-- (changed bank details are the most common invoice fraud) — bank_details_doc says which filed
-- document they came from, so the change can be checked against it.
-- RLS on commerce_suppliers is unchanged. Idempotent.

alter table commerce_suppliers add column if not exists bank_name text;
alter table commerce_suppliers add column if not exists bank_account_name text;
alter table commerce_suppliers add column if not exists bank_account_number text;
alter table commerce_suppliers add column if not exists bank_branch_code text;
alter table commerce_suppliers add column if not exists bank_details_doc uuid;
alter table commerce_suppliers add column if not exists bank_details_at timestamptz;
comment on column commerce_suppliers.bank_account_number is
    'From the supplier''s own invoice (verified on the page); never overwritten automatically';
