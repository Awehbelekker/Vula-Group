-- 268_prospects.sql — businesses the Vula team would like as clients, scanned before they are one.
--
-- 13 Nov 2026 (Ian: "scan any client's website and social and do a pitch"). A prospect's website
-- is read with the import's read-only mode and the report is kept here (vula/prospects.py), so
-- Master can look at a business without creating a tenant for it. "Make them a tenant" fills in
-- the new-tenant form; tenant_id records which tenant it became.
--
--   report     what the scan found, what's missing, what Vula would do, the pitch (jsonb)
--   socials    the social page links Master entered (links only: nothing reads them)
--
-- Read and written only by the API with the service key, for Master. RLS on, no policies.
-- Idempotent.

create table if not exists vula_prospects (
    id          uuid primary key default gen_random_uuid(),
    name        text not null,
    url         text not null,
    socials     jsonb not null default '[]'::jsonb,
    status      text not null default 'scanning' check (status in ('scanning', 'done', 'failed')),
    report      jsonb,
    error       text,
    tenant_id   text,
    created_by  text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create index if not exists idx_prospects_created on vula_prospects (created_at desc);
alter table vula_prospects enable row level security;
