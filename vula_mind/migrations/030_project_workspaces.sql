-- 030_project_workspaces.sql — "Claude Projects" for Vula: per-project chat threads,
-- a project brief (custom instructions), and Vula-native to-dos (sync to ClickUp).
create table if not exists vula_project_threads (
    id         uuid primary key default gen_random_uuid(),
    tenant_id  text not null,
    project    text not null,
    title      text default 'New chat',
    created_by text,
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);
create table if not exists vula_project_messages (
    id         uuid primary key default gen_random_uuid(),
    tenant_id  text not null,
    thread_id  uuid not null,
    role       text not null,                 -- user | assistant
    content    text,
    created_at timestamptz default now()
);
create table if not exists vula_project_briefs (
    tenant_id  text not null,
    project    text not null,
    brief      text,                          -- per-project "custom instructions"
    updated_at timestamptz default now(),
    primary key (tenant_id, project)
);
create table if not exists vula_project_tasks (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      text not null,
    project        text not null,
    title          text,
    status         text default 'open',       -- open | done
    assignee       text,
    due            date,
    clickup_task_id text,
    source         text default 'vula',        -- vula | clickup
    created_at     timestamptz default now(),
    updated_at     timestamptz default now()
);
create index if not exists idx_pthreads on vula_project_threads (tenant_id, project, updated_at desc);
create index if not exists idx_pmessages on vula_project_messages (thread_id, created_at);
create index if not exists idx_ptasks on vula_project_tasks (tenant_id, project, status);
