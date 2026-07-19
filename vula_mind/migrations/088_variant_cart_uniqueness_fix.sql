-- 088_variant_cart_uniqueness_fix.sql — fixes a real bug found during end-to-end verification
-- of migration 087 (product variants): commerce_cart_items has had a table-level
-- UNIQUE(cart_id, product_id) constraint since migration 002, which predates variants. Two
-- DIFFERENT variants of the SAME product added to the same cart (e.g. Small-Red then
-- Small-Blue) violate that constraint and the second add_to_cart() call throws a raw DB error —
-- confirmed live against production data before this fix.
--
-- Fix: replace the single constraint with two partial unique indexes that mean the same thing
-- variant-per-variant: only one cart line for a product with NO variant (variant_id IS NULL,
-- matching today's behavior for any non-variant product exactly), and only one cart line per
-- SPECIFIC variant (variant_id IS NOT NULL) — so two different variants of the same product can
-- coexist as separate lines, matching service.add_to_cart's application-level matching logic.

alter table commerce_cart_items drop constraint if exists commerce_cart_items_cart_id_product_id_key;

create unique index if not exists idx_cart_items_no_variant
    on commerce_cart_items(cart_id, product_id)
    where variant_id is null;

create unique index if not exists idx_cart_items_with_variant
    on commerce_cart_items(cart_id, product_id, variant_id)
    where variant_id is not null;
