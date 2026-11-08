-- 252_delivery_enabled.sql — a shop can be collection only (8 Nov 2026).
--
-- The WhatsApp setup now asks a shop how customers get their orders (delivery, collection or
-- both; vula/onboard_wa.py). Collection already had collection_enabled (migration 177); delivery
-- was always assumed. delivery_enabled = false means orders are collected only: button checkout
-- (vula/commerce/button_checkout.py) skips Delivery and place_order refuses a delivery.
-- Existing rows keep delivering (default true). Idempotent; the table already has RLS.
alter table commerce_order_settings add column if not exists delivery_enabled boolean not null default true;
