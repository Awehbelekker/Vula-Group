-- 273_estimates.sql — estimates built up from a trade's confirmed build-up.
--
-- 14 Nov 2026 (trade costing, phase 3). Each estimate is kept with every line as it was priced:
-- the quantity, the price, where the price came from and its date. Phase 4 compares an estimate
-- with what the job actually cost. Money is integer cents. Idempotent.

create table if not exists vula_estimates (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    created_by   text,
    title        text not null,
    items        jsonb not null default '[]'::jsonb,
    totals       jsonb not null default '{}'::jsonb,
    total_cents  bigint not null default 0,
    created_at   timestamptz not null default now()
);
create index if not exists vula_estimates_tenant_idx on vula_estimates (tenant_id, created_at desc);
alter table vula_estimates enable row level security;
