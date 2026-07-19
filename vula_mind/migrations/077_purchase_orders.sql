-- 077_purchase_orders.sql — supplier purchase orders + auto-reorder thresholds (P3.3).
-- Suppliers existed only for bill-matching (invoice scan); there was no way to actually order
-- more stock from them, nor any low-stock → reorder suggestion.

alter table commerce_products
    add column if not exists reorder_threshold integer,     -- suggest reorder when stock_quantity <= this
    add column if not exists reorder_qty integer,            -- suggested order quantity
    add column if not exists default_supplier_id uuid;        -- who to order this product from

create table if not exists commerce_purchase_orders (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null,
    supplier_id uuid,
    supplier_name text,                 -- denormalised snapshot (survives supplier edits/deletes)
    status text not null default 'draft',   -- draft | sent | received | cancelled
    items jsonb not null default '[]',      -- [{product_id, name, quantity, unit_cost_cents}]
    total_cents integer not null default 0,
    notes text,
    created_at timestamptz not null default now(),
    sent_at timestamptz,
    received_at timestamptz
);

create index if not exists idx_purchase_orders_tenant on commerce_purchase_orders(tenant_id, created_at desc);
