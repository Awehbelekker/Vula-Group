-- 310_flow_coach_pool.sql — a school with no free coach asks a coach from another school
-- (vula/commerce/coach_pool.py, 25 Nov 2026).
--
-- flow_pool_coaches: a coach's OWN choice to be asked by other schools of the same partner. It is
-- written only from that coach's login; their home school has no say in it.
-- flow_coach_offers: one row per coach a school asked for one lesson at one time. The first coach
-- to accept gets it (one accepted offer a request, by a partial unique index). `share_bp` (the
-- asking school's coach share) and `fee_bp` (the partner's fee, of the lesson's price) are kept as
-- they were when the school asked.
-- commerce_lesson_requests gains who the guest coach is and the fee that goes with the lesson.
--
-- Both tables are read across schools by design (a coach reads offers from other schools), so RLS
-- is on with no policy: only the service role (the API) reads or writes them. Idempotent.

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
