-- Vula Group — Migration 060: Expense claims (who paid + reimbursement tracking)
-- Run in: Supabase Dashboard > SQL Editor > New Query > Run
-- ============================================================================
-- Turns commerce_expenses into a proper expense-CLAIM ledger: every business
-- spend or out-of-pocket claim records WHO paid it, whether they must be
-- reimbursed, and where the money went (category/account/project). Builds on
-- the columns added by migrations 009/055/058 (status, project, vat_cents,
-- account_code) — this adds the "who + reimburse" dimension.

alter table commerce_expenses
    add column if not exists paid_by       text,          -- phone/id of who paid or claims
    add column if not exists paid_by_name  text,          -- human name (staff/owner/contractor)
    add column if not exists reimbursable  boolean not null default false,
    add column if not exists reimbursed_at timestamptz,   -- when the person was paid back
    add column if not exists channel       text,          -- 'whatsapp' | 'dashboard' | 'scanner'
    add column if not exists receipt_doc_id text,          -- link to the stored receipt document
    add column if not exists notes         text,
    add column if not exists updated_at    timestamptz not null default now();

-- The status set widens from (pending/paid/cancelled) to the claim lifecycle.
-- Drop the old 009 CHECK if present and re-add the fuller one.
do $$
begin
    if exists (select 1 from pg_constraint where conname = 'commerce_expenses_status_check') then
        alter table commerce_expenses drop constraint commerce_expenses_status_check;
    end if;
end $$;

alter table commerce_expenses
    add constraint commerce_expenses_status_check
    check (status in ('pending','submitted','approved','paid','reimbursed','rejected','cancelled'));

-- The 006 category CHECK is too narrow (commerce-only). Drop it so expenses can
-- carry any accounting-chart category/account_code learned by the Books module.
do $$
begin
    if exists (select 1 from pg_constraint where conname = 'commerce_expenses_category_check') then
        alter table commerce_expenses drop constraint commerce_expenses_category_check;
    end if;
end $$;

create index if not exists idx_commerce_expenses_paid_by
    on commerce_expenses (tenant_id, paid_by);
create index if not exists idx_commerce_expenses_reimbursable
    on commerce_expenses (tenant_id, reimbursable, reimbursed_at);
create index if not exists idx_commerce_expenses_status
    on commerce_expenses (tenant_id, status);
