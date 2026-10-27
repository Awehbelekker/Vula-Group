-- 226_project_shares.sql — a project's tracker, shared with its client by link.
--
-- 27 Oct 2026 (Ian, DIGG: a project tracker with budget vs actual, what the client paid
-- directly vs what they paid the business, labour / subcontractors / materials, and a link the
-- client can open; "choose per project" what the client sees). Belladonna's final account was
-- built by hand; vula/commerce/project_tracker.py now builds it from the books, and this table
-- holds the links:
--   view         price      — the contract, what was invoiced and paid, what is still owed;
--                             the business's own costs and margin stay private
--                open_book  — cost-plus: what the business spent per trade, what the client
--                             paid suppliers directly, the agreed fee, and the balance
--   token_hash   sha256 of the link's random token — the token itself is never stored, so the
--                table alone can't open a link
--   expires_at   every link ends (180 days by default); revoked_at ends it sooner
--
-- Read and written only by the API with the service key; the public page looks a link up by
-- its hash. RLS on, no policies: nothing else can read it. Idempotent.

create table if not exists vula_project_shares (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      text not null,
    project        text not null,
    view           text not null check (view in ('price', 'open_book')),
    token_hash     text not null unique,
    created_by     text,
    created_at     timestamptz not null default now(),
    expires_at     timestamptz not null default (now() + interval '180 days'),
    revoked_at     timestamptz,
    last_viewed_at timestamptz,
    views          integer not null default 0
);
create index if not exists idx_project_shares_tenant on vula_project_shares (tenant_id, project);
alter table vula_project_shares enable row level security;
