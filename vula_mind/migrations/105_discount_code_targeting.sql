-- 105_discount_code_targeting.sql — first-order-only + per-customer usage cap for discount
-- codes. Third gap closed from the marketing capability audit: migration 091 deliberately
-- scoped codes to storewide/global-usage-limit only ("no per-customer usage cap... easy to
-- add as extra columns later without breaking this") — this is that addition.
--
-- Product/category targeting and BOGO are NOT included here — those change the shape of the
-- discount computation itself (per-line-item, not per-subtotal) and are a bigger, separate
-- change; this migration only adds columns that fit the existing subtotal-based model.

alter table commerce_discount_codes
    add column if not exists first_order_only boolean not null default false,
    add column if not exists per_customer_limit integer;   -- null = no per-customer cap
