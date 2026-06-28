-- 031_ai_usage.sql — per-tenant cost visibility (COGS).
-- LLM token usage per tenant/day/model, plus a daily snapshot of storage + vectors so
-- the master admin can see true cost per tenant and detect runaway spend.
create table if not exists vula_ai_usage (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    day           date not null default (now() at time zone 'utc')::date,
    model         text not null,
    calls         integer not null default 0,
    prompt_tokens bigint not null default 0,
    completion_tokens bigint not null default 0,
    est_cost_usd  numeric not null default 0,
    updated_at    timestamptz default now(),
    unique (tenant_id, day, model)
);
create index if not exists idx_ai_usage_tenant on vula_ai_usage (tenant_id, day);

create table if not exists vula_infra_snapshot (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    day           date not null default (now() at time zone 'utc')::date,
    vectors       bigint default 0,
    storage_mb    numeric default 0,
    est_cost_usd  numeric default 0,
    updated_at    timestamptz default now(),
    unique (tenant_id, day)
);
create index if not exists idx_infra_tenant on vula_infra_snapshot (tenant_id, day);
