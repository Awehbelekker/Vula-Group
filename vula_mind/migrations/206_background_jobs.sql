-- 206_background_jobs.sql — one row per (tenant, background job kind): is it running, and how
-- did the last run end. Shared by both uvicorn workers.
--
-- 2026-10-13 (SeedleMe's website import): a long run's progress lived in one worker's memory.
-- The dashboard's progress polls landed on either worker, so it showed "not running" mid-run,
-- the owner pressed Import again, and four runs hit the shop at once until Shopify refused them
-- ("429 Too Many Requests") — 52 of ~290 products came through. Now the status is read and the
-- "already running" check made here. Idempotent. RLS on; the service role (the API) only.

create table if not exists vula_background_jobs (
    tenant_id   text not null,
    kind        text not null,            -- 'site_import', …
    running     boolean not null default false,
    status      jsonb not null default '{}'::jsonb,
    started_at  timestamptz,
    updated_at  timestamptz not null default now(),
    primary key (tenant_id, kind)
);
alter table vula_background_jobs enable row level security;
