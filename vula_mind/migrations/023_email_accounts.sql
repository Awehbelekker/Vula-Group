-- 023_email_accounts.sql — per-tenant IMAP/SMTP mailbox connection.
--
-- For any custom-domain mailbox without OAuth (GoDaddy Workspace, cPanel/webmail,
-- Zoho, etc.). Password stored encrypted (Fernet, prefixed). App-passwords strongly
-- recommended. Vula reads/searches the inbox, files attachments into the KB, and
-- saves drafts to the Drafts folder (send is optional).

create table if not exists vula_email_accounts (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null unique,
    email        text not null,                 -- the mailbox address (username)
    from_name    text,
    imap_host    text not null,
    imap_port    integer not null default 993,
    smtp_host    text,
    smtp_port    integer not null default 465,
    secret       text not null,                 -- encrypted password (fernet:… / plain:…)
    send_mode    text not null default 'draft', -- draft | send
    status       text not null default 'connected',
    last_error   text,
    connected_by text,
    connected_at timestamptz,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);
create index if not exists idx_vula_email_accounts_tenant on vula_email_accounts (tenant_id);
