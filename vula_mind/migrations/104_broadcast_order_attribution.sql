-- 104_broadcast_order_attribution.sql — link an order back to the broadcast that likely
-- drove it, closing the gap flagged in the marketing capability audit: the broadcast funnel
-- (vula/api/links.py, migration 064) already tracks clicks per recipient, but nothing
-- connected a click to a resulting order, so campaign ROI couldn't be computed from
-- anything in the schema. Last-click attribution: if a customer clicked a broadcast link
-- within the last 7 days, the order they go on to place credits that broadcast
-- (vula/commerce/service.py:_attribute_broadcast, called from create_order).

alter table commerce_orders
    add column if not exists attributed_broadcast_id uuid;

create index if not exists idx_orders_attributed_broadcast
    on commerce_orders (attributed_broadcast_id) where attributed_broadcast_id is not null;
