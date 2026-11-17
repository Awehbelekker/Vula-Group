-- 286_project_client_emails.sql — emails sent to a project's client from the business's own mailbox.
--
-- 16 Nov 2026 (Ian, DIGG): "send him approval so that we can prove he has access". The owner writes
-- the email on the project's Money tab and confirms; Vula sends it from the business's connected
-- mailbox, sends a copy to that mailbox, and keeps this row: who sent it, to whom, when, the subject
-- and the text, and whether it went (vula/commerce/project_emails.py).
-- Idempotent.

create table if not exists vula_project_client_emails (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    project     text not null,
    recipients  text[] not null default '{}',
    subject     text not null,
    body        text not null,
    sent_by     text,
    status      text not null default 'sending' check (status in ('sending', 'sent', 'failed')),
    error       text,
    copy_to     text,
    sent_at     timestamptz,
    created_at  timestamptz not null default now()
);

create index if not exists vula_project_client_emails_tenant_project
    on vula_project_client_emails (tenant_id, project, created_at desc);

alter table vula_project_client_emails enable row level security;
