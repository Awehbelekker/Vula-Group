-- ============================================================
-- Vula Group — Migration 056: persist a project's BoQ / contract value
-- Takeoff BoQ results live only in an in-memory dict (lost on restart), so a project's
-- contract value fell back to the manual budget. This persists the BoQ total as the project's
-- contract value (one per project, upsertable), so the unified project financials show a real
-- contract figure. Run in Supabase SQL editor.
-- ============================================================

create table if not exists vula_project_boq (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    project      text not null,
    title        text,
    total_cents  bigint not null default 0,     -- BoQ / contract value in cents
    source_job   text,                          -- takeoff job id it came from (if any)
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    unique (tenant_id, project)
);
create index if not exists idx_project_boq_tenant on vula_project_boq (tenant_id);
