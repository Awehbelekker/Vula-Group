-- 310_parcels_without_order.sql — send a parcel that belongs to no order (25 Nov 2026).
--
-- Delivery › Couriers › Send a parcel books a courier for something that isn't a sale: a sample,
-- a return, documents. It is a commerce_shipments row like any other, with no order behind it:
--   order_id    may now be empty
--   recipient   {name, mobile_number, email} — who it goes to (an order's booking reads the order)
--   reference   the business's own words for it, shown on the waybill
--   request_id  made by the screen once per parcel; unique per business, so a second tap or a
--               retry finds the first booking instead of paying for a second
-- Idempotent.

alter table commerce_shipments alter column order_id drop not null;
alter table commerce_shipments add column if not exists recipient jsonb;
alter table commerce_shipments add column if not exists reference text;
alter table commerce_shipments add column if not exists request_id text;
create unique index if not exists uq_shipments_request on commerce_shipments (tenant_id, request_id)
    where request_id is not null;
create index if not exists idx_shipments_parcels on commerce_shipments (tenant_id, created_at desc)
    where order_id is null;
