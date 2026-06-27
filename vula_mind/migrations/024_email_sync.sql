-- 024_email_sync.sql — email auto-sync: contacts/supplier/co-worker library + state.
--
-- A background loop pulls NEW mail, builds a contact directory (internal co-workers vs
-- external contacts/suppliers) and auto-files genuine work attachments into the KB.

create table if not exists vula_email_contacts (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    email         text not null,
    name          text,
    domain        text,
    kind          text not null default 'external',  -- internal | external | supplier | client
    message_count integer not null default 1,
    first_seen    timestamptz,
    last_seen     timestamptz,
    created_at    timestamptz not null default now(),
    unique (tenant_id, email)
);
create index if not exists idx_vula_email_contacts_tenant on vula_email_contacts (tenant_id, kind);

-- Sync state lives on the account row.
alter table vula_email_accounts add column if not exists auto_sync     boolean default true;
alter table vula_email_accounts add column if not exists last_sync_uid integer default 0;
alter table vula_email_accounts add column if not exists last_sync_at  timestamptz;
