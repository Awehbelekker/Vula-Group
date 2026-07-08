-- ============================================================
-- Vula Group — Migration 044: order payment methods (pay-on-delivery / EFT / online)
-- Lets a customer choose how to pay when ordering over WhatsApp, and gives every order
-- a recorded method. Adds per-tenant enabled methods + EFT bank details.
-- Run in Supabase SQL editor.
-- ============================================================

-- 1. Record the chosen method on the order (online = card via gateway, cod = pay on
--    delivery, eft = manual bank transfer). Nullable so legacy/web orders are unaffected.
alter table commerce_orders
    add column if not exists payment_method text;   -- 'online' | 'cod' | 'eft'

-- 2. Per-tenant: which methods are offered + the EFT bank details to show the customer.
alter table commerce_order_settings
    add column if not exists payment_methods text[] not null default array['online','cod','eft'],
    add column if not exists eft_details text;

-- Off the Hook: offer all three; EFT details are a placeholder until Staci sets the real
-- bank account from the dashboard (Order Workflow panel).
update commerce_order_settings
    set payment_methods = array['online','cod','eft']
    where tenant_id = 'off-the-hook' and payment_methods is null;
