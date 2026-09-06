-- 154_merchant_profiles.sql — decide a merchant's account ONCE, not once per transaction.
--
-- 2026-09-06, Ian asked why Vula can't research a merchant like "The Crazy Store" and allocate
-- from what it sells. Measured on production, accounting.categorize_batch was filing the same
-- merchant differently on different lines — Crazy Store x8 split 4 cost_of_sales / 4
-- owner_drawings, Dis-Chem x8 split 7/1, Table Bay had a payment OUT filed as income — because
-- it decides per transaction in batches of 40 (separate model calls) and is told nothing about
-- the merchant beyond the raw statement string.
--
-- Caches hits AND misses, exactly like commerce_geo_cache (migration 098): a merchant that
-- cannot be identified must never be researched twice. Unlike geo_cache this is TENANT-scoped,
-- because "Pick n Pay" is stock for a caterer and personal spend for an architect.

create table if not exists commerce_merchant_profiles (
    tenant_id      text not null,
    merchant_key   text not null,        -- merchants.merchant_key(): "crazy store"
    display_name   text,                 -- "The Crazy Store"
    what_they_sell text,                 -- researched trade; NULL = researched, unidentifiable
    account_code   text,                 -- agreed allocation; NULL until decided
    confidence     text,                 -- 'confident' | 'ambiguous'
    decided_by     text,                 -- 'research' | 'owner'
    asked_at       timestamptz,          -- owner asked; stamped so we ask ONCE, ever
    researched_at  timestamptz,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now(),
    primary key (tenant_id, merchant_key)
);

-- The background pass's only scheduled query: "ambiguous, nobody asked yet".
create index if not exists commerce_merchant_profiles_ask_idx
    on commerce_merchant_profiles (tenant_id)
    where confidence = 'ambiguous' and asked_at is null;

alter table commerce_merchant_profiles enable row level security;

drop policy if exists commerce_merchant_profiles_service on commerce_merchant_profiles;
create policy commerce_merchant_profiles_service on commerce_merchant_profiles
    for all to service_role using (true) with check (true);
