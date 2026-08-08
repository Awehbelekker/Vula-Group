-- ============================================================
-- Vula Group — Migration 123: track how a purchase order was actually sent
-- commerce_purchase_orders (migration 077) already has status/sent_at, but "sent" was always
-- just a manual flag — nothing dispatched anything. This adds the channel used once real
-- dispatch (email/WhatsApp) exists (vula/commerce/purchase_orders.py).
-- Run in Supabase SQL editor.
-- ============================================================

alter table commerce_purchase_orders add column if not exists sent_channel text;
