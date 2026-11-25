-- Paste into the Supabase SQL editor (clear the editor first). Safe to run twice.
begin;
alter table commerce_products add column if not exists specs jsonb;

create table if not exists commerce_product_guides (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    token           text not null unique,
    title           text not null,
    client_name     text,
    intro           text,
    categories      jsonb not null default '[]'::jsonb,
    product_ids     jsonb not null default '[]'::jsonb,
    show_prices     boolean not null default false,
    active          boolean not null default true,
    views           integer not null default 0,
    last_viewed_at  timestamptz,
    created_by      text,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now()
);
create index if not exists idx_product_guides_tenant on commerce_product_guides (tenant_id, created_at desc);

alter table commerce_product_guides enable row level security;
drop policy if exists "tenant_isolation" on commerce_product_guides;
create policy "tenant_isolation" on commerce_product_guides
    using (tenant_id = current_setting('app.tenant_id', true));

comment on table commerce_product_guides is 'A product guide a business sends its clients: a secret link to its range with spec sheets, optionally prices, and a WhatsApp stock check.';
insert into vula_schema_migrations (filename, number, sha256, applied_by, baseline)
values ('310_product_guides.sql', 310, '061dac440e98be5701aa5325a6a2ba37b5973a3adefbb6c6ef0e1c553b2a1a0b', 'sql-editor', false) on conflict do nothing;
commit;
