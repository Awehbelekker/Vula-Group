-- 029_team_members.sql — per-tenant team directory.
-- Each member has their own WhatsApp (for notifications + inbound recognition), an access
-- scope (which dashboard modules they see) and per-event notification subscriptions.
create table if not exists vula_team_members (
    id         uuid primary key default gen_random_uuid(),
    tenant_id  text not null,
    name       text,
    email      text,                                   -- links to login (Supabase Auth) if they sign in
    whatsapp   text,                                   -- digits / E.164-ish
    role       text default 'staff',                   -- owner | manager | bookkeeper | staff
    access     jsonb default '[]'::jsonb,              -- module keys: ["finances","followups",...]
    notify     jsonb default '[]'::jsonb,              -- event types: ["which_project","followup_digest",...]
    active     boolean default true,
    created_at timestamptz default now(),
    updated_at timestamptz default now(),
    unique (tenant_id, whatsapp)
);
create index if not exists idx_team_tenant on vula_team_members (tenant_id, active);
create index if not exists idx_team_email  on vula_team_members (tenant_id, email);
