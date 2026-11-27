-- 314_product_rivals.sql — rival products for reps, and guides only the team opens
-- (vula/commerce/product_rivals.py, 26 Nov 2026). Numbered 314: 313 is
-- the lesson paid by Tap to Pay (313_lesson_paid_by_tap.sql), merged the same day.
--
-- Floornet's field guide lists, for each of its products, the competing products it stands
-- against ("Tier product from B_ / from Nouwens") so a rep can answer "what's your version of
-- X?". That list is the business's own sales knowledge and never goes to a client:
--
--   commerce_product_rivals — one row per rival product: the brand, its product name, an optional
--       note. Its own table, NOT a column on commerce_products, because the public product routes
--       hand out whole product rows. Read only by team routes and by a guide made for the team.
--   commerce_product_guides.audience — 'client' (the default, never shows rivals) or 'team'
--       (shows them, and its page says it is not for clients).
--
-- Idempotent.

create table if not exists commerce_product_rivals (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    product_id  uuid not null,                      -- commerce_products.id, the business's own
    brand       text,
    name        text not null,
    note        text,
    sort_order  integer not null default 0,
    created_by  text,
    created_at  timestamptz not null default now()
);
create index if not exists idx_product_rivals_product on commerce_product_rivals (tenant_id, product_id);

alter table commerce_product_rivals enable row level security;
drop policy if exists "tenant_isolation" on commerce_product_rivals;
create policy "tenant_isolation" on commerce_product_rivals
    using (tenant_id = current_setting('app.tenant_id', true));

comment on table commerce_product_rivals is 'Competing products a business''s product stands against: for its reps only, never shown to clients.';

alter table commerce_product_guides add column if not exists audience text not null default 'client';
alter table commerce_product_guides drop constraint if exists commerce_product_guides_audience_check;
alter table commerce_product_guides add constraint commerce_product_guides_audience_check
    check (audience in ('client', 'team'));
