-- 032_invoice_identity.sql — close the invoicing audit gaps.
-- (1) Company-identity metadata was never collected per tenant, so non-OTH invoices
--     fell back to a title-cased tenant_id with blank email/phone/reg.
-- (2) VAT handling: registered? prices inclusive of VAT?
-- (3) Harden sequential numbering against the read-max-then-increment race.
alter table commerce_invoice_settings add column if not exists company_name      text;
alter table commerce_invoice_settings add column if not exists company_email     text;
alter table commerce_invoice_settings add column if not exists company_phone      text;
alter table commerce_invoice_settings add column if not exists company_reg        text;   -- company registration no.
alter table commerce_invoice_settings add column if not exists vat_registered     boolean not null default true;
alter table commerce_invoice_settings add column if not exists prices_include_vat boolean not null default false;

-- One invoice number per tenant — prevents duplicate numbers under concurrency.
create unique index if not exists uniq_invoice_number_per_tenant
    on commerce_invoices (tenant_id, invoice_number);
