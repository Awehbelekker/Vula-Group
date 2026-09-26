-- 177_order_collection.sql — let a shop offer collection (pickup) on WhatsApp orders.
-- Off by default: a shop opts in from Settings → Order workflow. A collection order needs no
-- delivery address and is charged no delivery fee. Idempotent; the table already has RLS.
alter table commerce_order_settings add column if not exists collection_enabled boolean not null default false;
alter table commerce_order_settings add column if not exists collection_note text;  -- where/when to collect
