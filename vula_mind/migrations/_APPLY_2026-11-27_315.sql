-- Paste into the Supabase SQL editor (clear the editor first). Safe to run twice.
begin;
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
insert into vula_schema_migrations (filename, number, sha256, applied_by, baseline)
values ('315_product_rivals.sql', 315, 'e382843cfe578a835e54e9b76594eff70e78839b03830784bb3d1cdc5c1f69d8', 'sql-editor', false) on conflict do nothing;
commit;
select 'done' as result,
  (select count(*) from information_schema.tables where table_name = 'commerce_product_rivals') as rivals_table,
  (select count(*) from information_schema.columns where table_name = 'commerce_product_guides' and column_name = 'audience') as audience_column;
