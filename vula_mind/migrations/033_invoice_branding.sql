-- 033_invoice_branding.sql — invoice customization: "trading as" + logo, and a
-- per-tenant saved clients/suppliers directory (so you pick a saved party instead of
-- retyping their details on every invoice).
alter table commerce_invoice_settings add column if not exists trading_as text;   -- "Pty Ltd t/a <brand>"
alter table commerce_invoice_settings add column if not exists logo_url   text;   -- shown in the invoice header

create table if not exists commerce_invoice_clients (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    kind        text not null default 'client',   -- client | supplier
    name        text not null,
    email       text,
    phone       text,
    address     text,
    vat_number  text,
    notes       text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create index if not exists idx_invoice_clients_tenant on commerce_invoice_clients (tenant_id, kind);
alter table commerce_invoice_clients enable row level security;
drop policy if exists "tenant_isolation" on commerce_invoice_clients;
create policy "tenant_isolation" on commerce_invoice_clients
    using (tenant_id = current_setting('app.tenant_id', true));
