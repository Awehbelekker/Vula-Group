-- 261_bank_accounts.sql — a business's bank accounts, and the one project an account can belong to.
--
-- 11 Nov 2026 (Ian, DIGG): "Bank statements should not be allocated to a project, it's the whole
-- business, unless the tenant adds a bank account to a certain project." DIGG's FNB account
-- ending 6574 is Porterfield's own account; its main business account (…4607) is the whole business.
--
--   commerce_bank_accounts   one row per account Vula has read a statement of (number as printed,
--                            digits only); `project` set by the owner = every line from this
--                            account is that project's. Null = the whole business.
--   commerce_bank_transactions.bank_account   the account a line came from (null: read before 261).
--   commerce_bank_transactions.project_source why a line is on its project: owner | account | bill
--                            | named | worker | rule (null: before 261, or not on a project).
-- Idempotent.

create table if not exists commerce_bank_accounts (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    account_number  text not null,
    bank            text,
    name            text,
    holder          text,
    project         text,
    first_seen      date,
    last_seen       date,
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    unique (tenant_id, account_number)
);
alter table commerce_bank_accounts enable row level security;

alter table commerce_bank_transactions add column if not exists bank_account text;
alter table commerce_bank_transactions add column if not exists project_source text;
create index if not exists commerce_bank_transactions_account_idx
    on commerce_bank_transactions (tenant_id, bank_account);

-- 12 Nov 2026 (Ian): "Private account is kept for Richard only, if he wants to ask questions about
-- it; this is his data and only visible to him." A document or an account can be private to one
-- team member. Private documents live in their own table, so nothing that reads the business's
-- documents (Documents, reports, project files, client links) can include one by accident; their
-- knowledge-base chunks carry private_to and are used only when answering that member.
alter table commerce_bank_accounts add column if not exists private_to text;   -- vula_team_members.id

create table if not exists vula_private_documents (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    member_id   text not null,               -- vula_team_members.id: the only person who sees it
    filename    text not null,
    doc_id      text,                         -- its knowledge-base chunks (private_to = member_id)
    file_url    text,
    category    text,
    summary     text,
    fields      jsonb not null default '{}'::jsonb,
    source      text,
    content_hash text,
    created_at  timestamptz not null default now()
);
alter table vula_private_documents enable row level security;
create index if not exists vula_private_documents_member_idx on vula_private_documents (tenant_id, member_id);

-- Documents already filed for the business that belong to one member: the server moves each one
-- (row + knowledge chunks) and marks it done.
create table if not exists vula_private_moves (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           text not null,
    filed_document_id   uuid not null,
    member_id           text not null,
    done_at             timestamptz,
    error               text,
    created_at          timestamptz not null default now()
);
alter table vula_private_moves enable row level security;
