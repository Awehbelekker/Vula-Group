-- 012_field_ops.sql
-- Field operations moved from ephemeral SQLite to Supabase so contractors,
-- tasks, evidence, and sign-offs are durable and reportable alongside the rest
-- of Vula (orders, products, document extractions).

create table if not exists vula_field_contractors (
    id          text primary key,
    tenant_id   text not null,
    name        text not null,
    phone       text not null,            -- E.164 digits, e.g. 27821234567
    trade       text default '',
    active      boolean default true,
    created_at  text default ''
);
create index if not exists idx_field_contractors_phone on vula_field_contractors (phone);
create index if not exists idx_field_contractors_tenant on vula_field_contractors (tenant_id);

create table if not exists vula_field_assignments (
    id            text primary key,
    tenant_id     text not null,
    project_id    text not null,
    contractor_id text not null,
    role          text default 'contractor',  -- contractor|site_manager|architect|quantity_surveyor
    assigned_at   text default ''
);
create index if not exists idx_field_assign_project on vula_field_assignments (project_id);
create index if not exists idx_field_assign_contractor on vula_field_assignments (contractor_id);

create table if not exists vula_field_tasks (
    id           text primary key,
    tenant_id    text not null,
    project_id   text not null,
    title        text not null,
    trade        text default '',
    status       text default 'pending',  -- pending|in_progress|awaiting_sign_off|complete|rejected
    assigned_to  text default '',          -- contractor_id
    due_date     text default '',
    notes        text default '',
    created_at   text default '',
    updated_at   text default ''
);
create index if not exists idx_field_tasks_project on vula_field_tasks (project_id);
create index if not exists idx_field_tasks_assigned on vula_field_tasks (assigned_to);
create index if not exists idx_field_tasks_tenant on vula_field_tasks (tenant_id);

create table if not exists vula_field_evidence (
    id            text primary key,
    task_id       text not null,
    contractor_id text not null,
    photo_url     text not null,           -- Supabase Storage URL (or local path fallback)
    caption       text default '',         -- includes the AI vision assessment
    submitted_at  text default ''
);
create index if not exists idx_field_evidence_task on vula_field_evidence (task_id);

create table if not exists vula_field_sign_offs (
    id             text primary key,
    task_id        text not null,
    approver_phone text not null,
    status         text not null,          -- approved|rejected
    notes          text default '',
    approved_at    text default ''
);
create index if not exists idx_field_signoffs_task on vula_field_sign_offs (task_id);

create table if not exists vula_field_walkthroughs (
    id          text primary key,
    tenant_id   text not null,
    project_id  text not null,
    title       text not null,
    items       jsonb default '[]'::jsonb,
    status      text default 'pending',
    created_at  text default ''
);
