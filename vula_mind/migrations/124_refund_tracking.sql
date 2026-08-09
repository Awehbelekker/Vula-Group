-- 124_refund_tracking.sql — track real gateway refunds against orders/invoices.
-- Orders already have a 'refunded' status (migration 002); invoices represent a refund as a
-- credit note instead (no 'refunded' status exists in commerce_invoices' CHECK constraint —
-- deliberately not changed here). Both tables gain the same tracking columns so a real Yoco
-- refund call (POST /api/checkouts/{id}/refund, keyed off the yoco_checkout_id already
-- captured at checkout-creation time) has somewhere to record its outcome.

alter table commerce_orders add column if not exists refund_status text;              -- 'pending' | 'failed'
alter table commerce_orders add column if not exists refunded_amount_cents integer;
alter table commerce_orders add column if not exists refunded_at timestamptz;
alter table commerce_orders add column if not exists yoco_refund_id text;

alter table commerce_invoices add column if not exists refund_status text;            -- 'pending' | 'failed'
alter table commerce_invoices add column if not exists refunded_amount_cents integer;
alter table commerce_invoices add column if not exists refunded_at timestamptz;
alter table commerce_invoices add column if not exists yoco_refund_id text;
