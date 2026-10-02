-- 191: phases of a project (2026-10-02, Ian: "Sporty phase 2 is part of Sporty TV — phase two of
-- the first project"; "how can a tenant add phases or more projects for one client, each with
-- their own BOQ and filed invoices and slips?"). A phase is its own project (own BOQ, budget,
-- documents, job costing, programme) with parent_id pointing at the main project; the main
-- project's page adds its phases together. Idempotent.

alter table vula_projects add column if not exists parent_id uuid references vula_projects(id) on delete set null;
alter table vula_projects add column if not exists phase text;
create index if not exists vula_projects_parent_idx on vula_projects (tenant_id, parent_id);
