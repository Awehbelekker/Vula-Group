-- 237_employee_tasks.sql — the business's settings for each task of its AI employees.
--
-- 31 Oct 2026 (Ian's whiteboard: workflow → task → prompt / tools / data). Each AI employee's work
-- is split into tasks, defined as files in vula_mind/employees/<employee>/tasks/<task>/ (the
-- defaults every business starts from, core/skills/employees.py). This table only holds what a
-- business changed: a task switched off, and its own instructions for that task ("final accounts in
-- our layout, no VAT line"). No row = on, no instructions.
--
-- Read and written only by the API with the service key. RLS on, no policies. Idempotent.

create table if not exists vula_employee_tasks (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    employee      text not null,
    task          text not null,
    enabled       boolean not null default true,
    instructions  text,
    updated_by    text,
    updated_at    timestamptz not null default now()
);
create unique index if not exists uq_employee_tasks on vula_employee_tasks (tenant_id, employee, task);
alter table vula_employee_tasks enable row level security;
