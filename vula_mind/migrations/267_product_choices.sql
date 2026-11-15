-- 267_product_choices.sql — a product's choices that don't change the price, kept with each line.
--
-- 13 Nov 2026 (Ian, onboarding Kelp Gear: board bags made to order). A bag's price depends on its
-- board size, which is a variant (087). Its colour, trim, nose and tail style don't change the
-- price, so they weren't variants, and an order line had nowhere to keep them: a customer could
-- say "olive green with a fish tail" and the order would only say "9'6"".
--
--   commerce_products.choices        [{"name": "Primary Colour", "values": ["Olive Green", …]}, …]
--                                    — what the customer chooses from; null or [] = none.
--   commerce_cart_items.choices      {"Primary Colour": "Olive Green", …} — what they chose.
--   commerce_order_items.choices     the same, copied when the order is placed.
--
-- Two bags of the same size in different colours are two cart lines, so the cart's unique
-- indexes (088) now include the choices. Idempotent; all three tables already have RLS enabled.

alter table commerce_products
    add column if not exists choices jsonb;

alter table commerce_cart_items
    add column if not exists choices jsonb not null default '{}'::jsonb;

alter table commerce_order_items
    add column if not exists choices jsonb not null default '{}'::jsonb;

drop index if exists idx_cart_items_no_variant;
create unique index if not exists idx_cart_items_no_variant_choices
    on commerce_cart_items(cart_id, product_id, choices)
    where variant_id is null;

drop index if exists idx_cart_items_with_variant;
create unique index if not exists idx_cart_items_with_variant_choices
    on commerce_cart_items(cart_id, product_id, variant_id, choices)
    where variant_id is not null;

alter table commerce_products enable row level security;
alter table commerce_cart_items enable row level security;
alter table commerce_order_items enable row level security;
