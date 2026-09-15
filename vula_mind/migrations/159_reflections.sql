-- 159_reflections.sql — move the HRM reflection / routing-hint store off ephemeral local disk.
--
-- 2026-09-15: core/memory/reflection.py stored routing hints (which model tier worked for
-- which kind of question) in a local SQLite file. Checked Railway's own service config for
-- Vula-Group directly: "volumes": [] — no persistent volume is mounted, so /data and ~/.vula
-- are both plain container-local disk, wiped on every redeploy. Every other "learned"
-- mechanism on the platform (voice profiles — migration 119/120, learned answers — migration
-- 042/150, merchant profiles — migration 154) already lives in Supabase for exactly this
-- reason; reflections was the one exception.
--
-- This is also the real foundation of the Mass Mind architecture (see the design doc shared
-- separately): a store that resets on every deploy can never compound the platform-wide
-- intelligence the later Mass Mind rollup phases depend on. tenant_id was only added to the
-- old SQLite store hours earlier (see the "fence the reflection store" change) — this table
-- starts fenced from day one, so there's no backfill/migration-of-data concern: the old store
-- is simply retired, not migrated row-for-row (it held at most a redeploy's worth of history).

create table if not exists vula_reflections (
    id                bigint generated always as identity primary key,
    tenant_id         text not null,
    graph_id          text not null,
    goal              text not null,
    primary_skill     text,
    winning_tier      text,
    outcome_score     double precision,
    merge_strategy    text,
    total_latency_ms  integer,
    what_worked       text,
    what_to_try_next  text,
    skills_used       jsonb,
    model_tiers_used  jsonb,
    created_at        timestamptz not null default now()
);

-- get_routing_hints()'s real query shape: tenant_id + recency/score-ranked candidates, keyword
-- matching against `goal` done in Python over that bounded set (same convention already used
-- by vula/escalation.py::find_learned_answer, rather than a server-side LIKE-per-keyword scan).
create index if not exists vula_reflections_tenant_idx
    on vula_reflections (tenant_id, outcome_score desc, created_at desc);

alter table vula_reflections enable row level security;

-- Backend-only table — never read or written by a tenant's own dashboard session, only by the
-- API process itself (agent_runner.py, memory_recall.py, and the /metrics + /agent/stats
-- operator views). Same pattern as commerce_merchant_profiles (migration 154).
drop policy if exists vula_reflections_service on vula_reflections;
create policy vula_reflections_service on vula_reflections
    for all to service_role using (true) with check (true);
