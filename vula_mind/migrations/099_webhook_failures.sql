-- 099_webhook_failures.sql (renumbered from 076 — collided with 076_platform_feedback.sql) —
-- records inbound WhatsApp webhook messages that threw while being
-- processed (P3.2). Previously an unhandled exception per-message just became a log line and,
-- worse, could crash the whole webhook batch — now each message dispatch is isolated and any
-- failure is both recorded here (for the Health tab) and skipped so the rest of the batch still
-- processes.

create table if not exists vula_webhook_failures (
    id uuid primary key default gen_random_uuid(),
    tenant_id text,
    phone text,
    msg_type text,
    error text,
    created_at timestamptz not null default now()
);

create index if not exists idx_webhook_failures_created on vula_webhook_failures(created_at desc);
