-- ============================================================
-- Vula Group — Migration 055: link invoices & expenses to a project
-- So a project's true financial picture (contract/budget · invoiced · paid-in · spent · net)
-- can be aggregated in one place. Invoices and expenses previously had no project link, so a
-- construction project (e.g. DIGG's HPC) showed almost nothing. Run in Supabase SQL editor.
-- ============================================================

alter table commerce_invoices add column if not exists project text;
alter table commerce_expenses add column if not exists project text;

create index if not exists idx_commerce_invoices_project on commerce_invoices (tenant_id, project);
create index if not exists idx_commerce_expenses_project on commerce_expenses (tenant_id, project);
