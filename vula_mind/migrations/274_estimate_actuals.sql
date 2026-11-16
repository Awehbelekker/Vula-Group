-- 274_estimate_actuals.sql — learn from what the job actually cost (trade costing, phase 4).
--
-- 15 Nov 2026. An estimate can belong to a project, so it can be compared with what was paid on
-- that project (vula_price_observations). When the work is finished, a component whose bought
-- quantity is far from the build-up becomes a suggestion for an owner or manager to accept or
-- dismiss; accepting saves a new version of the build-up. Nothing changes until a person says so.
-- Idempotent.

alter table vula_estimates add column if not exists project text;
create index if not exists vula_estimates_project_idx on vula_estimates (tenant_id, project);

create table if not exists vula_buildup_suggestions (
    id               uuid primary key default gen_random_uuid(),
    tenant_id        text not null,
    estimate_id      uuid not null,
    buildup_id       uuid not null,
    project          text,
    item             text not null,
    spec             jsonb not null default '{}'::jsonb,
    component        text not null,
    label            text,
    kind             text not null default 'material',
    field            text not null,               -- waste_pct | qty_per_unit
    current_value    numeric not null,
    suggested_value  numeric not null,
    estimated_qty    numeric,
    bought_qty       numeric,
    unit             text,
    evidence         jsonb not null default '[]'::jsonb,
    status           text not null default 'pending',   -- pending | accepted | dismissed
    new_buildup_id   uuid,
    decided_by       text,
    decided_at       timestamptz,
    created_at       timestamptz not null default now(),
    unique (tenant_id, estimate_id, buildup_id, component, field)
);
create index if not exists vula_buildup_suggestions_tenant_idx
    on vula_buildup_suggestions (tenant_id, status, created_at desc);
alter table vula_buildup_suggestions enable row level security;
