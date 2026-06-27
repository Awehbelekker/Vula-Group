-- 028_email_followups.sql — emails awaiting a reply + reminder state.
-- Inbound external emails that look like a question/request/scheduling are tracked so
-- Vula can nudge the user on WhatsApp until they're handled.
create table if not exists vula_email_followups (
    id               uuid primary key default gen_random_uuid(),
    tenant_id        text not null,
    email_uid        integer,
    sender           text,
    sender_name      text,
    subject          text,
    preview          text,
    received_at      timestamptz,
    reason           text,                       -- question | request | schedule
    status           text default 'open',        -- open | done | snoozed
    last_reminded_at timestamptz,
    created_at       timestamptz default now(),
    unique (tenant_id, email_uid)
);
create index if not exists idx_vula_followups_tenant on vula_email_followups (tenant_id, status);
