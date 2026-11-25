-- Paste into the Supabase SQL editor (clear the editor first). Safe to run twice.
begin;
create table if not exists kb_staff_targets (
    tenant_id   text not null,
    member_id   text not null,
    period      text not null default 'day' check (period in ('day', 'week')),
    cents       integer not null check (cents > 0),
    updated_at  timestamptz not null default now(),
    primary key (tenant_id, member_id)
);
alter table kb_staff_targets enable row level security;
drop policy if exists "tenant_isolation" on kb_staff_targets;
create policy "tenant_isolation" on kb_staff_targets using (tenant_id = current_setting('app.tenant_id', true));
comment on table kb_staff_targets is 'A team member''s own earnings target (day or week). Private to that person.';
insert into vula_schema_migrations (filename, number, sha256, applied_by, baseline)
values ('307_staff_targets.sql', 307, '082e070de01be8bd74988c2eda1ee38b74bc64fe2c35a5a6846c0e619cb25ce1', 'sql-editor', false) on conflict do nothing;
commit;
select 'done' as result, count(*) as new_tables from information_schema.tables where table_schema = 'public' and table_name = 'kb_staff_targets';
