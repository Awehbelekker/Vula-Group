-- 080_order_channel_manual.sql — fixes a live bug found during verification of the P1.3 manual
-- order feature: commerce_orders.channel had a CHECK constraint allowing only ('web','whatsapp')
-- (migration 002), so admin_create_manual_order's channel='manual' failed every single time with
-- a 500 (commerce_orders_channel_check violation). Widen the constraint to include it.

alter table commerce_orders drop constraint if exists commerce_orders_channel_check;
alter table commerce_orders add constraint commerce_orders_channel_check
    check (channel in ('web', 'whatsapp', 'manual'));
