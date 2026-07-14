-- ============================================================
-- Vula Group — Migration 058: accounting core (chart of accounts, categorisation, VAT)
-- Stage 2 of moving tenants off Xero: allocate every transaction to a chart-of-accounts category,
-- track VAT per-tenant, and produce P&L / VAT / general-ledger for the accountant. Categorisation
-- learns from the owner's corrections. Run in Supabase SQL editor.
-- ============================================================

-- Chart of accounts, per tenant (seeded with SA small-business defaults on first use).
create table if not exists commerce_accounts (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    code          text not null,                 -- short code e.g. 'sales', 'packaging'
    name          text not null,
    type          text not null default 'expense',   -- income | expense | asset | liability | equity
    vat_treatment text not null default 'standard',  -- standard | zero | exempt | none
    deductible    boolean not null default true,     -- tax-deductible (expenses)
    sort          int not null default 0,
    active        boolean not null default true,
    created_at    timestamptz not null default now(),
    unique (tenant_id, code)
);
create index if not exists idx_commerce_accounts_tenant on commerce_accounts (tenant_id, active, sort);

-- Learned categorisation rules (mirrors vula_filing_rules): a signal → an account code.
create table if not exists commerce_txn_rules (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    signal       text not null,                  -- normalised value (supplier/reference/token)
    signal_type  text not null default 'token',  -- supplier | reference | account | token
    account_code text not null,
    hits         int not null default 1,
    last_used    timestamptz not null default now(),
    created_at   timestamptz not null default now(),
    unique (tenant_id, signal, account_code)
);
create index if not exists idx_commerce_txn_rules_tenant on commerce_txn_rules (tenant_id, signal);

-- Allocation + VAT on each parsed bank transaction.
alter table commerce_bank_transactions
    add column if not exists account_code   text,
    add column if not exists vat_cents       bigint,
    add column if not exists vat_treatment    text,
    add column if not exists categorized_by   text;   -- learned | ai | owner

-- Input VAT + account on expenses.
alter table commerce_expenses
    add column if not exists vat_cents    bigint,
    add column if not exists account_code text;

alter table commerce_accounts   enable row level security;
alter table commerce_txn_rules  enable row level security;
drop policy if exists "tenant_isolation" on commerce_accounts;
create policy "tenant_isolation" on commerce_accounts using (tenant_id = current_setting('app.tenant_id', true));
drop policy if exists "tenant_isolation" on commerce_txn_rules;
create policy "tenant_isolation" on commerce_txn_rules using (tenant_id = current_setting('app.tenant_id', true));
