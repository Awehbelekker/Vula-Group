-- ============================================================
-- Vula Group — Migration 074: link a reconciled bank credit to the ORDER it paid
-- Bank reconciliation (migration 057) only ever matched credits to commerce_invoices. A
-- customer paying by EFT for a regular product order (payment_method='eft', which the
-- checkout flow has always offered) had no matching path at all — a proof-of-payment email
-- for an order fell straight through to the generic "which project should I file it under?"
-- prompt instead of being reconciled. Run in Supabase SQL editor.
-- ============================================================

alter table commerce_bank_transactions
    add column if not exists matched_order_id uuid references commerce_orders(id);
create index if not exists idx_commerce_bank_txns_matched_order
    on commerce_bank_transactions (matched_order_id);
