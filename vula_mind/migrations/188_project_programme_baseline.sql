-- 188: run a project from its programme and against one signed cost baseline (2026-09-29, Judy /
-- DIGG, Atlantis Foods Paarden Island: "a four-week programme, very tight, connected to cost …
-- task the staff … what is due for the day per room … we signed cost revision 10 … all project
-- costs managed against that … any variation flagged with a variation order").
-- Idempotent; both tables already have RLS (vula_field_tasks: 113, vula_project_boq: 056).

-- The baseline: which signed document it is, and that later estimates never replace it.
alter table vula_project_boq add column if not exists baseline_doc_id text;
alter table vula_project_boq add column if not exists baseline_locked boolean not null default false;
alter table vula_project_boq add column if not exists baseline_set_at timestamptz;

-- Programme tasks are field-ops tasks with a room, a start date and where they came from, so the
-- existing DONE / photo-evidence / sign-off flow works for them unchanged.
alter table vula_field_tasks add column if not exists room text;
alter table vula_field_tasks add column if not exists start_date text;
alter table vula_field_tasks add column if not exists assignee_name text;
alter table vula_field_tasks add column if not exists source text;
alter table vula_field_tasks add column if not exists source_doc_id text;
create index if not exists idx_field_tasks_programme
  on vula_field_tasks (tenant_id, project_id, start_date, due_date);
