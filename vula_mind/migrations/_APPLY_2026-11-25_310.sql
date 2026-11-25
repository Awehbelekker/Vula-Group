-- Paste into the Supabase SQL editor (clear the editor first). Safe to run twice.
begin;
create table if not exists flow_pool_coaches (
    tenant_id   text not null,                            -- the coach's home school
    member_id   text not null,                            -- vula_team_members.id
    open        boolean not null default false,
    updated_at  timestamptz not null default now(),
    primary key (tenant_id, member_id)
);
alter table flow_pool_coaches enable row level security;
comment on table flow_pool_coaches is 'A coach''s own choice to be asked for lessons by other schools of the same partner.';
create table if not exists flow_coach_offers (
    id               uuid primary key default gen_random_uuid(),
    tenant_id        text not null,                       -- the school asking
    request_id       uuid not null,                       -- commerce_lesson_requests.id
    coach_tenant_id  text not null,                       -- the coach's home school
    coach_member_id  text not null,
    coach_name       text not null,
    starts_at        timestamptz not null,
    minutes          integer not null check (minutes between 15 and 600),
    share_bp         integer not null check (share_bp between 0 and 10000),
    fee_bp           integer not null check (fee_bp between 0 and 5000),
    status           text not null default 'offered'
                     check (status in ('offered', 'accepted', 'declined', 'withdrawn', 'taken')),
    asked_by         text,
    created_at       timestamptz not null default now(),
    answered_at      timestamptz
);
create unique index if not exists uq_coach_offers_one_accepted on flow_coach_offers (request_id) where status = 'accepted';
create unique index if not exists uq_coach_offers_live on flow_coach_offers (request_id, coach_tenant_id, coach_member_id)
    where status in ('offered', 'accepted');
create index if not exists idx_coach_offers_coach on flow_coach_offers (coach_tenant_id, coach_member_id, status);
create index if not exists idx_coach_offers_request on flow_coach_offers (tenant_id, request_id);
alter table flow_coach_offers enable row level security;
comment on table flow_coach_offers is 'A school asks a coach from another school for one lesson; the first to accept gets it.';
alter table commerce_lesson_requests add column if not exists guest_coach_tenant text;
alter table commerce_lesson_requests add column if not exists guest_coach_id text;
alter table commerce_lesson_requests add column if not exists pool_fee_bp integer;
insert into vula_schema_migrations (filename, number, sha256, applied_by, baseline)
values ('310_flow_coach_pool.sql', 310, 'e3a6d64122d902500b883217529b0a3a1efe6b520fb93adc2adbea8db35dbb81', 'sql-editor', false) on conflict do nothing;
commit;
select 'done' as result, count(*) as new_tables from information_schema.tables where table_schema = 'public' and table_name in ('flow_pool_coaches', 'flow_coach_offers');
