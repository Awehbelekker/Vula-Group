-- 072_admin_audit.sql — audit trail for admin actions (master panel, Phase C5).
-- Every write through /v1/master/* records who did what to which tenant, when. Append-only.
-- Backend-only (service key); RLS with no public policy keeps it out of tenant query paths.

create table if not exists vula_admin_audit (
    id           bigserial primary key,
    actor_email  text,
    actor_id     text,
    action       text not null,          -- e.g. tenant_created, tenant_updated, user_role_changed
    tenant_id    text,                   -- the tenant acted ON (null for platform-wide actions)
    detail       jsonb not null default '{}'::jsonb,
    created_at   timestamptz not null default now()
);

create index if not exists idx_admin_audit_time   on vula_admin_audit (created_at desc);
create index if not exists idx_admin_audit_tenant on vula_admin_audit (tenant_id, created_at desc);

alter table vula_admin_audit enable row level security;
