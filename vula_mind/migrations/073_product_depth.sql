-- 073_product_depth.sql — e-commerce product depth (2026-07-17).
-- 1) Multi-image gallery (images jsonb; image_url stays as the cover for back-compat).
-- 2) Sale pricing with an optional end date (charged everywhere via the effective-price helper).
-- 3) Sort order + archive (soft delete — hard delete broke order-history references).
-- 4) Categories become PER-TENANT DATA: the hardcoded 5-fish CHECK constraint blocked every
--    non-seafood tenant (e.g. Kelp Boardbags). Existing OTH keys are seeded so nothing breaks.

alter table commerce_products
    add column if not exists images jsonb,                    -- ["url1","url2",...]; image_url = cover
    add column if not exists sale_price_cents integer,        -- NULL = no sale
    add column if not exists sale_ends_at timestamptz,        -- NULL = open-ended sale
    add column if not exists sort_order integer default 0,
    add column if not exists archived boolean not null default false;

-- Category becomes free text (per-tenant categories table is the source of truth).
alter table commerce_products drop constraint if exists commerce_products_category_check;

create table if not exists commerce_categories (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    key         text not null,          -- slug used on products.category
    label       text not null,          -- display name
    sort_order  integer not null default 0,
    created_at  timestamptz not null default now(),
    unique (tenant_id, key)
);
create index if not exists idx_categories_tenant on commerce_categories (tenant_id, sort_order);

alter table commerce_categories enable row level security;
drop policy if exists "tenant_isolation" on commerce_categories;
create policy "tenant_isolation" on commerce_categories
    using (tenant_id = current_setting('app.tenant_id', true));

-- Seed Off the Hook's existing five categories (idempotent).
insert into commerce_categories (tenant_id, key, label, sort_order)
values
    ('off-the-hook', 'fresh_fish',     'Fresh Fish',     1),
    ('off-the-hook', 'fresh_chicken',  'Fresh Chicken',  2),
    ('off-the-hook', 'frozen_chicken', 'Frozen Chicken', 3),
    ('off-the-hook', 'frozen_seafood', 'Frozen Seafood', 4),
    ('off-the-hook', 'extras',         'Extras',         5)
on conflict (tenant_id, key) do nothing;
