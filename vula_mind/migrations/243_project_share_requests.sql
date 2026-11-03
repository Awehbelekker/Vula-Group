-- 243_project_share_requests.sql — a client asks to download a project's documents.
--
-- 3 Nov 2026 (Ian): "We want to allow the client to view, but they can request if they want to
-- download." A client's project link (vula_project_shares, migration 226) now lists the project's
-- client documents, view-only (pages shown as watermarked images). Downloading needs the
-- business's yes: the client asks from the page, the owner approves or declines (WhatsApp or the
-- dashboard), and an approval opens downloads on that link for a limited time.
--
--   scope           'all' (the whole client copy, as a zip) or one vula_filed_documents id
--   status          requested → approved | declined
--   approved_until  downloads work until then (approved only)
--
-- Read and written by the backend with the service key only. RLS on, no policies. Idempotent.
create table if not exists vula_project_share_requests (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    share_id        uuid not null references vula_project_shares(id) on delete cascade,
    project         text not null,
    scope           text not null,
    status          text not null default 'requested' check (status in ('requested', 'approved', 'declined')),
    requested_at    timestamptz not null default now(),
    decided_at      timestamptz,
    decided_by      text,
    approved_until  timestamptz
);
create index if not exists idx_share_requests_share on vula_project_share_requests (share_id, status);
create index if not exists idx_share_requests_tenant on vula_project_share_requests (tenant_id, project, status);
alter table vula_project_share_requests enable row level security;
