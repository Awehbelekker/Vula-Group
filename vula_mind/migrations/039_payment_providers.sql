-- 039_payment_providers.sql — pluggable SA payment gateways per tenant.
-- Each tenant connects one or more gateways (Yoco, PayFast, Peach, iKhokha, Ozow, Paystack);
-- one is the default used for invoice pay-links + checkout. Credentials are per-tenant,
-- stored server-side only (never in the frontend).
create table if not exists vula_payment_providers (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    provider    text not null,                     -- yoco | payfast | peach | ikhokha | ozow | paystack
    credentials jsonb not null default '{}'::jsonb, -- provider-specific keys (secret)
    mode        text not null default 'live',       -- live | test
    is_default  boolean not null default false,
    active      boolean not null default true,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now(),
    unique (tenant_id, provider)
);
create index if not exists idx_payment_providers_tenant on vula_payment_providers (tenant_id, active);
alter table vula_payment_providers enable row level security;
drop policy if exists "tenant_isolation" on vula_payment_providers;
create policy "tenant_isolation" on vula_payment_providers
    using (tenant_id = current_setting('app.tenant_id', true));
