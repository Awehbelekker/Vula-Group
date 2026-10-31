-- 236_employees.sql — the business's settings for each of its AI employees.
--
-- 31 Oct 2026 (Ian): every business gets a ready-made team of AI employees behind its one
-- WhatsApp number — bookkeeper, sales and customer service, office administrator, marketing,
-- operations (core/skills/employees.py). The team itself is defined in code; this table only
-- holds what the owner changed: an employee switched off, and plain-words instructions for it
-- ("always quote on 30-day terms"). No row = on, no instructions.
--
-- Read and written only by the API with the service key. RLS on, no policies. Idempotent.

create table if not exists vula_employees (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    employee      text not null,
    enabled       boolean not null default true,
    instructions  text,
    updated_by    text,
    updated_at    timestamptz not null default now()
);
create unique index if not exists uq_employees_tenant_employee on vula_employees (tenant_id, employee);
alter table vula_employees enable row level security;
