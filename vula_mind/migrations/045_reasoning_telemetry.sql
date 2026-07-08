-- ============================================================
-- Vula Group — Migration 045: reasoning telemetry sink
-- Persistent, queryable home for the shared reasoning-telemetry envelope so live prod runs
-- accumulate (Railway's filesystem is ephemeral). Written by the LLM router (every routing
-- decision) and the VRL verify events. Read back via verified-reasoning/tools/pull_prod.py.
-- Backend-only (service key); no tenant-facing access. Run in Supabase SQL editor.
-- ============================================================

create table if not exists vula_reasoning_telemetry (
    id          bigserial primary key,
    schema      int  not null default 1,
    system      text not null,                 -- vula-llm-router | verified-reasoning
    run_id      text,
    task        text,                          -- POPIA: a task-type label or hash, NEVER raw prompt content
    outcome     text,                          -- local | cloud | accepted | retry | escalated | ...
    escalated   boolean not null default false,
    verifier    text,                          -- digg.calc | digg.boq | ... (VRL only)
    reason      text,                          -- local_first | complexity:* | local_unreliable | ... (router)
    tenant_id   text,
    extra       jsonb not null default '{}'::jsonb,
    created_at  timestamptz not null default now()
);

create index if not exists idx_reasoning_telemetry_system_time
    on vula_reasoning_telemetry (system, created_at desc);

-- Ops telemetry, not tenant data: enable RLS with no public policy, so only the service key
-- (which bypasses RLS) can read/write it. Keeps it out of any tenant-scoped query path.
alter table vula_reasoning_telemetry enable row level security;
