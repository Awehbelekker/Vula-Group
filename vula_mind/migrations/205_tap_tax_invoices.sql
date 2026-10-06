-- ============================================================
-- Vula Group — Migration 205: tap-to-pay tax invoices on request
-- One immutable VAT tax invoice per payment, issued only when the customer asks (WhatsApp "TAX" or the
-- receipt page). Numbers are sequential per tenant (unique (tenant_id, number)); supplier and buyer
-- details are SNAPSHOTTED so a later settings change can never alter an issued invoice. Tips are not on
-- the invoice. kb_tax_requests holds the short "send your company name and VAT number" conversation.
-- Idempotent. Run in Supabase SQL editor after 204.
-- ============================================================

create table if not exists kb_tax_invoices (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    payment_id    uuid not null references kb_payments(id),
    number        int  not null check (number > 0),
    buyer_name    text not null,
    buyer_vat     text not null check (buyer_vat ~ '^4[0-9]{9}$'),
    buyer_address text,
    bill_cents    bigint not null check (bill_cents > 0),       -- VAT-inclusive total on the invoice
    vat_cents     bigint not null check (vat_cents >= 0),
    supplier      jsonb  not null,                              -- name, vat, address, reg at issue time
    description   text,
    issued_at     timestamptz not null default now(),
    unique (payment_id),                                        -- one invoice per payment
    unique (tenant_id, number)                                  -- gap-free, never reused
);
create index if not exists idx_kb_tax_invoices_tenant on kb_tax_invoices (tenant_id, issued_at desc);

create or replace function kb_tax_invoice_immutable() returns trigger language plpgsql as $$
begin
    raise exception 'kb_tax_invoices is immutable (issue a credit note instead)';
end $$;
drop trigger if exists trg_kb_tax_invoice_immutable on kb_tax_invoices;
create trigger trg_kb_tax_invoice_immutable before update or delete on kb_tax_invoices
    for each row execute function kb_tax_invoice_immutable();

create table if not exists kb_tax_requests (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    payer_hash  text not null,
    payment_id  uuid not null references kb_payments(id),
    status      text not null default 'awaiting' check (status in ('awaiting','done','cancelled')),
    expires_at  timestamptz not null,
    created_at  timestamptz not null default now()
);
create index if not exists idx_kb_tax_requests_payer on kb_tax_requests (tenant_id, payer_hash, status);

alter table kb_tax_invoices enable row level security;
alter table kb_tax_requests enable row level security;
do $$
declare t text;
begin
    foreach t in array array['kb_tax_invoices','kb_tax_requests'] loop
        execute format('drop policy if exists "tenant_isolation" on %I', t);
        execute format('create policy "tenant_isolation" on %I using (tenant_id = current_setting(''app.tenant_id'', true))', t);
    end loop;
end $$;
