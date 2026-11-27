-- Paste into the Supabase SQL editor (clear the editor first). Safe to run twice.
begin;
alter table commerce_shipments alter column order_id drop not null;
alter table commerce_shipments add column if not exists recipient jsonb;
alter table commerce_shipments add column if not exists reference text;
alter table commerce_shipments add column if not exists request_id text;
create unique index if not exists uq_shipments_request on commerce_shipments (tenant_id, request_id)
    where request_id is not null;
create index if not exists idx_shipments_parcels on commerce_shipments (tenant_id, created_at desc)
    where order_id is null;
commit;
