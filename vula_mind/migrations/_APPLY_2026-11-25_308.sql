-- Paste into the Supabase SQL editor (clear the editor first). Safe to run twice.
begin;
create table if not exists commerce_lesson_ratings (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    request_id   uuid not null unique,
    stars        integer not null check (stars between 1 and 5),
    coach_id     text,
    coach_stars  integer check (coach_stars between 1 and 5),
    comment      text,
    created_at   timestamptz not null default now()
);
create index if not exists idx_lesson_ratings_tenant on commerce_lesson_ratings (tenant_id, created_at desc);
alter table commerce_lesson_ratings enable row level security;
drop policy if exists "tenant_isolation" on commerce_lesson_ratings;
create policy "tenant_isolation" on commerce_lesson_ratings using (tenant_id = current_setting('app.tenant_id', true));
comment on table commerce_lesson_ratings is 'A customer''s stars for a finished lesson: the school, and its coach. The comment is for the school only.';
insert into vula_schema_migrations (filename, number, sha256, applied_by, baseline)
values ('308_lesson_ratings.sql', 308, '93ebb44239d8c4913b0526e076dd2d57cd2bf6765357ebc479afd1ac0ca51fc6', 'sql-editor', false) on conflict do nothing;
commit;
select 'done' as result, count(*) as new_tables from information_schema.tables where table_schema = 'public' and table_name = 'commerce_lesson_ratings';
