-- 107_merchant_audit.sql — per-tenant audit trail for merchant admin actions.
-- The tenant-scoped counterpart to 072_admin_audit.sql (master's platform-wide log): closes the
-- "role changes, access-scope edits, and login management leave no trail an owner can query"
-- gap found in the 2026-07-24 admin-depth audit. Same append-only shape, one tenant's own view.

create table if not exists vula_merchant_audit (
    id           bigserial primary key,
    tenant_id    text not null,
    actor_email  text,
    actor_id     text,
    action       text not null,          -- e.g. member_added, member_role_changed, login_created
    detail       jsonb not null default '{}'::jsonb,
    created_at   timestamptz not null default now()
);

create index if not exists idx_merchant_audit_tenant_time
    on vula_merchant_audit (tenant_id, created_at desc);

alter table vula_merchant_audit enable row level security;
