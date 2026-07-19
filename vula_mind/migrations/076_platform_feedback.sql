-- ============================================================
-- Vula Group — Migration 076: platform feedback (tenant → Ian)
-- A tenant owner/team message that sounds like a question or concern about the Vula PLATFORM
-- itself (not their business) is forwarded to Ian's own WhatsApp AND logged here — durability
-- in case the WhatsApp forward fails or gets missed. Run in Supabase SQL editor.
-- ============================================================

create table if not exists vula_platform_feedback (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    phone        text not null,          -- the tenant team member's number
    sender_name  text,
    message      text not null,
    status       text not null default 'open',   -- open | resolved
    created_at   timestamptz not null default now()
);
create index if not exists idx_platform_feedback_status on vula_platform_feedback (status, created_at);
create index if not exists idx_platform_feedback_tenant on vula_platform_feedback (tenant_id);

-- No tenant-scoped RLS policy: this table is Ian's own inbox across all tenants, read only via
-- the service key (master panel / backend), never exposed to a tenant's own dashboard session.
alter table vula_platform_feedback enable row level security;
