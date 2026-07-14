-- ============================================================
-- Vula Group — Migration 059: casual labour register + bank-payment recognition
-- Construction/day-labour: workers paid daily/weekly, no invoice, not on payroll. Load a worker
-- once (name/ID/bank/rate); Vula recognises their payments off the statement, files them as casual
-- labour (no VAT, deductible), links them to a project (job costing), and learns the mapping.
-- Run in Supabase SQL editor.
-- ============================================================

create table if not exists commerce_workers (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      text not null,
    name           text not null,
    id_number      text,
    bank_account   text,
    phone          text,
    rate_cents     bigint,
    rate_period    text default 'daily',      -- daily | weekly
    type           text default 'casual',     -- casual | subcontractor
    default_project text,
    active         boolean not null default true,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);
create index if not exists idx_commerce_workers_tenant on commerce_workers (tenant_id, active);

-- Link a bank debit to the worker it paid + the project (site) it's costed to.
alter table commerce_bank_transactions
    add column if not exists worker_id uuid,
    add column if not exists project   text;

alter table commerce_workers enable row level security;
drop policy if exists "tenant_isolation" on commerce_workers;
create policy "tenant_isolation" on commerce_workers using (tenant_id = current_setting('app.tenant_id', true));

-- Seed the casual labour account for tenants whose chart already exists (no-VAT, deductible).
insert into commerce_accounts (tenant_id, code, name, type, vat_treatment, deductible, sort)
select distinct tenant_id, 'casual_labour', 'Casual labour', 'expense', 'none', true, 45
from commerce_accounts
on conflict (tenant_id, code) do nothing;
