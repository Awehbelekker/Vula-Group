-- 314_flow_partner_fees.sql — a partner's fee on lessons taught by a coach from another of its
-- schools: the rate, kept where the operator can change it, and a statement per school per month
-- (vula/commerce/partner_fees.py, 26 Nov 2026).
--
-- flow_partner_settings: one row per partner (FlowCrew). `pool_fee_bp` is the usual rate in
-- hundredths of a percent of the lesson's price; `overrides` is {tenant_id: bp} for a school on its
-- own rate; `pay_note` is how a school pays the partner. A lesson keeps the rate it was asked at
-- (flow_coach_offers.fee_bp), so a change here only reaches coaches asked afterwards.
-- flow_partner_fee_months: a school's fee for a finished month, frozen when the operator closes
-- the month, then marked paid or waived.
--
-- Both are the partner's, read across schools by the operator, so RLS is on with no policy: only
-- the service role (the API) reads or writes them. Idempotent.

create table if not exists flow_partner_settings (
    partner_id   text primary key,
    pool_fee_bp  integer check (pool_fee_bp between 0 and 5000),
    overrides    jsonb not null default '{}'::jsonb,
    pay_note     text,
    updated_by   text,
    updated_at   timestamptz not null default now()
);
alter table flow_partner_settings enable row level security;
comment on table flow_partner_settings is 'A partner''s fee rate on lessons taught by a guest coach: the usual rate, a school''s own rate, how to pay.';

create table if not exists flow_partner_fee_months (
    id           uuid primary key default gen_random_uuid(),
    partner_id   text not null,
    tenant_id    text not null,                           -- the school that owes the fee
    month        text not null check (month ~ '^\d{4}-(0[1-9]|1[0-2])$'),
    lessons      integer not null check (lessons >= 0),
    value_cents  bigint not null check (value_cents >= 0),
    fee_cents    bigint not null check (fee_cents >= 0),
    status       text not null default 'due' check (status in ('due', 'paid', 'waived')),
    note         text,
    closed_by    text,
    closed_at    timestamptz not null default now(),
    settled_by   text,
    settled_at   timestamptz,
    unique (partner_id, tenant_id, month)
);
create index if not exists idx_partner_fee_months_tenant on flow_partner_fee_months (tenant_id, month desc);
alter table flow_partner_fee_months enable row level security;
comment on table flow_partner_fee_months is 'A school''s fee to its partner for a finished month, frozen when the operator closes it.';
