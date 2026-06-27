-- 015_documents.sql — Vula Documents library (durable filed documents by project)
--
-- When a tenant WhatsApps a doc/photo, Vula reads + classifies it, ingests into the
-- KB, then FILES a durable copy here, linked to a project. When the project maps to a
-- ClickUp list, the file is also attached into ClickUp (one "Project Documents" task).

create table if not exists vula_filed_documents (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    project         text,                       -- matched project / ClickUp list name (null = unfiled)
    clickup_list_id text,                        -- ClickUp list the project maps to (if any)
    clickup_task_id text,                        -- the "Project Documents" task the file is attached to
    category        text,                        -- Invoice, BOQ, Fee Proposal, Drawing, etc.
    summary         text,
    fields          jsonb default '{}'::jsonb,   -- structured extraction (vendor, total, date, …)
    filename        text not null,
    file_url        text,                        -- durable Supabase Storage public URL
    mime            text,
    doc_id          text,                        -- KB doc id (Qdrant) for cross-reference
    source          text default 'whatsapp',     -- whatsapp | dashboard
    status          text not null default 'filed', -- filed | pending_project
    filed_by        text,                        -- WhatsApp phone / admin email
    created_at      timestamptz not null default now()
);

create index if not exists idx_vula_filed_documents_tenant   on vula_filed_documents (tenant_id, created_at desc);
create index if not exists idx_vula_filed_documents_project  on vula_filed_documents (tenant_id, project);
create index if not exists idx_vula_filed_documents_pending  on vula_filed_documents (tenant_id, filed_by, status);
