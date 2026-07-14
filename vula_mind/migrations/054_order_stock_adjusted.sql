-- ============================================================
-- Vula Group — Migration 054: track whether an order has adjusted stock
-- Stock was never decremented on sale. This flag lets Vula decrement product stock ONCE when an
-- order is paid/confirmed, and restore it ONCE if the order is cancelled or refunded — without
-- double-counting when the status changes several times. Run in Supabase SQL editor.
-- ============================================================

alter table commerce_orders
    add column if not exists stock_adjusted boolean not null default false;
