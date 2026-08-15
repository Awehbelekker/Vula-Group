-- 130_invoice_payments_and_cancellation.sql — partial payments + invoice cancellation.
--
-- Partial payments: `part_paid` has been a valid commerce_invoices.status since migration 008
-- but nothing has ever been able to reach it — deposit_cents (053) captures an expected deposit
-- at creation time but there was no way to actually RECORD a payment as it came in. This adds a
-- real payment ledger per invoice: each instalment is its own row (amount, method, when), so
-- multiple part-payments are supported and each one can post its own general-ledger entry on
-- its own date (accurate cash-flow timing, not one lump entry when the balance finally clears).
create table if not exists commerce_invoice_payments (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    invoice_id      uuid not null references commerce_invoices(id) on delete cascade,
    amount_cents    bigint not null check (amount_cents > 0),
    payment_method  text,                          -- cash | eft | card | other | null
    note            text,
    paid_at         timestamptz not null default now(),
    created_at      timestamptz not null default now()
);
create index if not exists idx_invoice_payments_tenant on commerce_invoice_payments (tenant_id, invoice_id);

alter table commerce_invoice_payments enable row level security;
drop policy if exists "tenant_isolation" on commerce_invoice_payments;
create policy "tenant_isolation" on commerce_invoice_payments
    using (tenant_id = current_setting('app.tenant_id', true));

-- Cancellation: invoices can already reach 'cancelled' per the CHECK constraint (migration 008)
-- but nothing has ever set it. Only ever allowed from draft/sent/overdue (never from paid or
-- part_paid — a paid invoice needing reversal already has the credit-note flow, which correctly
-- handles the ledger side; cancel never posts a ledger entry, same as order cancellation).
alter table commerce_invoices add column if not exists cancel_reason text;
alter table commerce_invoices add column if not exists cancelled_at timestamptz;

-- Running total, kept in sync by record_invoice_payment on every instalment — lets the
-- dashboard show/compute the real remaining balance from the normal invoice list fetch,
-- without a second round-trip to sum commerce_invoice_payments on every render.
alter table commerce_invoices add column if not exists total_paid_cents bigint not null default 0;
