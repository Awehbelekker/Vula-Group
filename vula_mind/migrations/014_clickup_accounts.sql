-- 014_clickup_accounts.sql
-- Per-tenant ClickUp connection (token + workspace/list IDs) and the mapping
-- between Vula field-ops tasks and their mirrored ClickUp tasks (two-way sync).

create table if not exists vula_clickup_accounts (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null unique,
    api_token     text not null,                 -- ClickUp personal API token
    team_id       text,                          -- ClickUp workspace/team id
    list_ids      jsonb default '{}'::jsonb,      -- {"default": "<list_id>", ...}
    space_id      text,
    status        text not null default 'connected', -- pending|connected|error|disconnected
    last_error    text,
    connected_by  text,
    connected_at  timestamptz,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
create index if not exists idx_clickup_accounts_tenant on vula_clickup_accounts (tenant_id);

-- Maps a Vula field-ops task id ↔ a ClickUp task id, for status/sign-off sync.
create table if not exists vula_field_clickup_map (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    field_task_id text not null,
    clickup_task_id text not null,
    created_at    timestamptz not null default now(),
    unique (field_task_id)
);
create index if not exists idx_field_clickup_map_tenant on vula_field_clickup_map (tenant_id);
create index if not exists idx_field_clickup_map_clickup on vula_field_clickup_map (clickup_task_id);
