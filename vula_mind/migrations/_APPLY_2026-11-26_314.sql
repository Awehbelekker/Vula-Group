-- Paste into the Supabase SQL editor (clear the editor first). Safe to run twice.
begin;
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
insert into vula_schema_migrations (filename, number, sha256, applied_by, baseline)
values ('314_flow_partner_fees.sql', 314, 'ee062609a17e3bdbf3ea190e06ce3ec13a46de42c84104cf1d2cfcedeca2fead', 'sql-editor', false) on conflict do nothing;
commit;
select 'done' as result, count(*) as new_tables from information_schema.tables where table_schema = 'public' and table_name in ('flow_partner_settings', 'flow_partner_fee_months');
