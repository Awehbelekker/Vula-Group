-- Vula: migration 301 (shifts and availability). Safe to run more than once.
-- Paste ALL of it into an empty Supabase SQL editor and press Run.
begin;

alter table kb_settings add column if not exists board_tips boolean not null default false;

create table if not exists kb_shifts (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    member_id    text,                                 -- null = an open shift, anyone on the team can take it
    starts_at    timestamptz not null,
    ends_at      timestamptz not null,
    section      text,                                 -- "Floor", "Bar", "Tables 1-6"
    note         text,
    status       text not null default 'draft',        -- draft | published | confirmed | declined | sick | cancelled
    reason       text,                                 -- why it was declined, or a note with "sick"
    published_at timestamptz,
    responded_at timestamptz,
    reminded_at  timestamptz,
    created_by   text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    check (ends_at > starts_at)
);
create index if not exists idx_kb_shifts_time on kb_shifts (tenant_id, starts_at);
create index if not exists idx_kb_shifts_member on kb_shifts (tenant_id, member_id, starts_at);
alter table kb_shifts enable row level security;
drop policy if exists "tenant_isolation" on kb_shifts;
create policy "tenant_isolation" on kb_shifts
    using (tenant_id = current_setting('app.tenant_id', true));

create table if not exists kb_availability (
    id         uuid primary key default gen_random_uuid(),
    tenant_id  text not null,
    member_id  text not null,
    kind       text not null,                          -- weekly (can work) | off (can't work these days)
    weekday    integer check (weekday is null or weekday between 0 and 6),   -- weekly: Mon=0
    start_time text,                                   -- weekly: "17:00"
    end_time   text,                                   -- weekly: "23:00"
    from_date  date,                                   -- off: first day away
    to_date    date,                                   -- off: last day away
    note       text,
    created_at timestamptz not null default now()
);
create index if not exists idx_kb_availability_member on kb_availability (tenant_id, member_id);
alter table kb_availability enable row level security;
drop policy if exists "tenant_isolation" on kb_availability;
create policy "tenant_isolation" on kb_availability
    using (tenant_id = current_setting('app.tenant_id', true));

comment on table kb_shifts is
    'A planned shift. Staff only ever see published ones; a shift with no member is open for the team to take.';

insert into vula_schema_migrations (filename, number, sha256, applied_by, baseline) values ('301_shifts.sql', 301, '1fbcb12dc3b05ff14d6b6dd77e1b4df8c43af87955a7ffc834a7af9abb2589af', 'sql-editor', false) on conflict do nothing;

commit;

select 'done' as result, count(*) as new_tables from information_schema.tables where table_schema = 'public' and table_name in ('kb_shifts','kb_availability');
