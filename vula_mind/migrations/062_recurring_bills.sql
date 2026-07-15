-- ============================================================
-- Vula Group — Migration 062: recurring bills (rent, utilities, subscriptions, supplier accounts)
-- "Rent, R15,000, due the 1st of every month." A scheduler creates a real commerce_expenses row
-- (status='pending', due_date set) ahead of each due date, feeding the existing "Payments due"
-- view (GET /admin/expenses/due) and pay action (PATCH /admin/expenses/{id}/pay) — both of which
-- already expect exactly this shape but, until now, had nothing that ever produced it (the
-- expense-claims workflow added in migration 060 defaults new claims to status='submitted').
-- Run in Supabase SQL editor.
-- ============================================================

create table if not exists commerce_recurring_bills (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      text not null,
    description    text not null,
    supplier       text,
    category       text not null default 'other',
    account_code   text,
    amount_cents   int  not null check (amount_cents > 0),
    cadence        text not null default 'monthly',      -- weekly | biweekly | monthly
    next_due       date not null,                         -- next date this bill is due
    lead_days      int  not null default 7,                -- create the pending expense this many days before due
    status         text not null default 'active',        -- active | paused | cancelled
    note           text,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);
create index if not exists idx_recurring_bills_due on commerce_recurring_bills (status, next_due);
create index if not exists idx_recurring_bills_tenant on commerce_recurring_bills (tenant_id, status);

alter table commerce_recurring_bills enable row level security;
drop policy if exists "tenant_isolation" on commerce_recurring_bills;
create policy "tenant_isolation" on commerce_recurring_bills
    using (tenant_id = current_setting('app.tenant_id', true));

-- Link a spawned expense back to the recurring bill that created it (traceability + a belt-and-
-- suspenders duplicate guard alongside the scheduler's own idempotency).
alter table commerce_expenses
    add column if not exists recurring_bill_id uuid references commerce_recurring_bills(id);
create index if not exists idx_commerce_expenses_recurring_bill
    on commerce_expenses (recurring_bill_id, due_date);
