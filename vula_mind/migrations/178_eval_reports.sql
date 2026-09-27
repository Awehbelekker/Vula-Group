-- 178_eval_reports.sql — model bake-off reports (vula_mind/evals, run from the Master panel).
--
-- The live tool-choice eval needs the OpenRouter key, which only the deployed API has, so the
-- Master panel starts a run on the server and each model's report lands here. Every tool in
-- the harness is stubbed and prompts use a no-data sandbox tenant: rows hold eval prompts,
-- the tool each model picked, latency and cost — never tenant or customer content.
-- Platform-level (no tenant_id): read and written only by the service role behind
-- require_master. RLS on with no policies = no access for anon/authenticated keys.
-- Idempotent.

create table if not exists vula_eval_reports (
    id           uuid primary key default gen_random_uuid(),
    model        text not null,
    skill        text,
    status       text not null default 'running' check (status in ('running', 'done', 'failed')),
    passed       integer,
    total        integer,
    report       jsonb,
    error        text,
    created_by   text,
    created_at   timestamptz not null default now(),
    finished_at  timestamptz
);

create index if not exists idx_eval_reports_created on vula_eval_reports (created_at desc);

alter table vula_eval_reports enable row level security;
