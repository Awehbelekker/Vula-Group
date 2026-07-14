-- ============================================================
-- Vula Group — Migration 057: bank reconciliation (move off Xero, no API)
-- Vula reads the weekly Capitec statement (emailed, password-protected PDF), unlocks it with the
-- account holder's ID number, parses the transactions, and reconciles them against invoices/expenses.
-- Run in Supabase SQL editor.
-- ============================================================

-- Parsed bank transactions from statements. Dedupe key prevents double-counting overlapping weeks.
create table if not exists commerce_bank_transactions (
    id                  uuid primary key default gen_random_uuid(),
    tenant_id           text not null,
    txn_date            date,
    description         text not null default '',
    amount_cents        bigint not null default 0,     -- absolute value in cents
    direction           text not null default 'in',    -- in (credit) | out (debit)
    balance_cents       bigint,
    reference           text,
    matched_invoice_id  uuid,
    matched_expense_id  uuid,
    match_status        text not null default 'unmatched',  -- matched | unmatched | ignored
    source_file         text,
    created_at          timestamptz not null default now(),
    unique (tenant_id, txn_date, amount_cents, description)
);
create index if not exists idx_bank_txn_tenant on commerce_bank_transactions (tenant_id, txn_date desc);
create index if not exists idx_bank_txn_status on commerce_bank_transactions (tenant_id, match_status);

alter table commerce_bank_transactions enable row level security;
drop policy if exists "tenant_isolation" on commerce_bank_transactions;
create policy "tenant_isolation" on commerce_bank_transactions
    using (tenant_id = current_setting('app.tenant_id', true));

-- Encrypted statement password (ID number) + bank name, stored on the tenant's email account row.
alter table vula_email_accounts
    add column if not exists statement_password text,   -- Fernet-encrypted (never plaintext)
    add column if not exists bank text;
