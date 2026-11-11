-- 258_bank_accounts.sql — a business's bank accounts, and the one project an account can belong to.
--
-- 11 Nov 2026 (Ian, DIGG): "Bank statements should not be allocated to a project, it's the whole
-- business, unless the tenant adds a bank account to a certain project." DIGG's FNB account
-- ending 6574 is Porterfield's own account; its main business account (…4607) is the whole business.
--
--   commerce_bank_accounts   one row per account Vula has read a statement of (number as printed,
--                            digits only); `project` set by the owner = every line from this
--                            account is that project's. Null = the whole business.
--   commerce_bank_transactions.bank_account   the account a line came from (null: read before 258).
--   commerce_bank_transactions.project_source why a line is on its project: owner | account | bill
--                            | named | worker | rule (null: before 258, or not on a project).
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
