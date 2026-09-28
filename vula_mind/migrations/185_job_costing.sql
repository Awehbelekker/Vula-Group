-- 185_job_costing.sql — bank lines carry a project and trade; allocations are learned; each
-- project has pricing terms (cost-plus fee %).
--
-- 2026-09-28 (Ian, DIGG): "Judy usually asks 10% on top of cost… Vula should see the
-- operational costs and labour costs based on transactions." Her own statement breakdown
-- (10 Jul – 12 Sep) showed HPC001 certificates R1,514,438 in against R1,560,782 out — the
-- job running R46k negative instead of +10%, and nothing in Vula could see it: bank lines got a
-- project only when they matched a registered worker, and there was no fee or margin anywhere.
-- vula/commerce/job_costing.py computes each project's received / cost by trade / overhead
-- share / profit against cost × (1 + fee_pct). Idempotent.

alter table commerce_bank_transactions
    add column if not exists payee text,
    add column if not exists trade text;
create index if not exists idx_bank_txn_project on commerce_bank_transactions (tenant_id, project);

-- "NELITHO WAGES" → HPC Bokaap / Labour; "hpc" (a description prefix) → HPC Bokaap.
create table if not exists commerce_allocation_rules (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    signal      text not null,
    signal_type text not null default 'merchant',   -- merchant | prefix
    project     text,
    trade       text,
    hits        integer not null default 1,
    updated_at  timestamptz not null default now()
);
create unique index if not exists idx_allocation_rules_key
    on commerce_allocation_rules (tenant_id, signal_type, signal,
                                  (coalesce(project, '')), (coalesce(trade, '')));
alter table commerce_allocation_rules enable row level security;

-- Pricing terms per project; project '*' is the tenant's default. cost_plus: the client pays
-- cost × (1 + fee_pct / 100).
create table if not exists vula_project_terms (
    tenant_id   text not null,
    project     text not null,
    pricing     text not null default 'cost_plus',
    fee_pct     numeric not null default 10,
    updated_at  timestamptz not null default now(),
    primary key (tenant_id, project)
);
alter table vula_project_terms enable row level security;
