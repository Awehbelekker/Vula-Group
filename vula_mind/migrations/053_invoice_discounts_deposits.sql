-- ============================================================
-- Vula Group — Migration 053: invoice discounts + deposits / partial payments
-- Adds an invoice-level discount amount and a deposit (amount already paid) so quotes and
-- invoices can show "less discount", a deposit, and the remaining balance due. Per-line
-- discount % lives inside the line_items JSONB (no column needed). Run in Supabase SQL editor.
-- ============================================================

alter table commerce_invoices
    add column if not exists discount_cents int not null default 0,   -- invoice-level discount amount
    add column if not exists deposit_cents  int not null default 0;   -- amount already paid (deposit)

-- 'part_paid' is a new status value (status is free text; no enum change needed).
