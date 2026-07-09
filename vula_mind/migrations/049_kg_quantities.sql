-- ============================================================
-- Vula Group — Migration 049: decimal quantities for weight-sold products
-- Products with sold_by='kg' price per kilogram, but quantity was INTEGER so a customer could
-- only order whole units. Widen quantity to numeric so they can order e.g. 0.5 / 1.5 kg.
-- Existing integer quantities are valid numerics — safe, backward-compatible. Run in Supabase.
-- ============================================================

alter table commerce_cart_items  alter column quantity type numeric(10,3);
alter table commerce_order_items alter column quantity type numeric(10,3);
-- The cart's CHECK (quantity > 0) and DEFAULT 1 remain valid on numeric.
