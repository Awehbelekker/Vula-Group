-- 016_projects.sql — Vula Projects, master code library, team directory.
--
-- Implements the practice's hierarchy natively in Vula:
--   Level 1  Project (name/number, client, status)
--   Level 2  Categories live on documents (Admin/Standards/Specs/Drawings/Team)
--   Master code library: standards (e.g. SANS 10400-A) uploaded ONCE, linked to
--   many projects via vula_project_codes (the per-project reference list).
--   Professional team directory with roles; documents carry submitted_by/role.
-- Run after 015_documents.sql.

-- Level 1 — Projects
create table if not exists vula_projects (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    name        text not null,
    number      text,                                  -- project / job number
    client      text,
    status      text not null default 'active',        -- active | on_hold | complete
    created_by  text,
    created_at  timestamptz not null default now()
);
create index if not exists idx_vula_projects_tenant on vula_projects (tenant_id, status);

-- Master code library — a standard lives in ONE place, linked to many projects.
create table if not exists vula_code_library (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    code_ref    text not null,                         -- e.g. STD-SANS10400-A
    title       text not null,                         -- SANS 10400 Part A: General Principles
    category    text not null default 'Standards',     -- Standards | Specification | …
    version     text,                                  -- e.g. Rev 2 / 2010
    status      text not null default 'current',       -- current | superseded
    file_url    text,                                  -- durable copy (Supabase Storage)
    doc_id      text,                                  -- KB doc id (Qdrant, source_type=reference)
    uploaded_by text,
    created_at  timestamptz not null default now(),
    unique (tenant_id, code_ref)
);
create index if not exists idx_vula_code_library_tenant on vula_code_library (tenant_id, status);

-- Per-project reference list — which codes apply to which project.
create table if not exists vula_project_codes (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    project_id  uuid not null references vula_projects (id) on delete cascade,
    code_id     uuid not null references vula_code_library (id) on delete cascade,
    created_at  timestamptz not null default now(),
    unique (project_id, code_id)
);
create index if not exists idx_vula_project_codes_project on vula_project_codes (project_id);

-- Professional team directory (per project, or tenant-wide when project_id is null).
create table if not exists vula_project_team (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    project_id  uuid references vula_projects (id) on delete cascade,
    name        text not null,
    role        text,                                  -- Principal Agent | Structural | Fire | …
    email       text,
    phone       text,
    created_at  timestamptz not null default now()
);
create index if not exists idx_vula_project_team_tenant on vula_project_team (tenant_id, project_id);

-- Document metadata for traceability + version control (extends 015).
alter table vula_filed_documents add column if not exists project_id    uuid;
alter table vula_filed_documents add column if not exists version        text;
alter table vula_filed_documents add column if not exists doc_status     text default 'current';   -- current | superseded
alter table vula_filed_documents add column if not exists submitted_by   text;
alter table vula_filed_documents add column if not exists submitted_role text;
