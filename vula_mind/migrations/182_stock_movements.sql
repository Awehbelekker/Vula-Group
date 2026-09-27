-- 182_stock_movements.sql — every stock change recorded, applied atomically; barcodes on products.
--
-- 2026-09-27 (barcode stock counting): stock_quantity was changed by five different paths —
-- product/variant PATCH, the WhatsApp update_stock tool, PO receive, the Smart Scanner's
-- delivery-note "apply stock" (in the browser), and order sales/refunds — three of them as a
-- read-modify-write that loses a concurrent change, and none left a record of who changed a
-- count or why. apply_stock_change() is now the one way stock moves outside checkout
-- reservations: one statement updates the quantity and in_stock and writes the movement.
-- Barcodes lived only on variants (0 variants in production); a product can now carry its
-- own barcode/sku, unique per tenant, so a scan resolves a simple product too. cost_cents
-- lets receiving record what stock cost, for stock-take variance and margins.
-- Idempotent.

alter table commerce_products add column if not exists barcode text;
alter table commerce_products add column if not exists sku text;
alter table commerce_products add column if not exists cost_cents integer;
alter table commerce_product_variants add column if not exists cost_cents integer;

create unique index if not exists idx_products_tenant_barcode
    on commerce_products (tenant_id, barcode) where barcode is not null;

create table if not exists commerce_stock_movements (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    product_id  uuid not null,
    variant_id  uuid,
    delta       integer not null,
    qty_after   integer,
    reason      text not null check (reason in ('count', 'receive', 'adjust', 'sale', 'refund', 'cancel', 'import')),
    ref_type    text,               -- 'stock_count' | 'purchase_order' | 'order' | 'scan' | …
    ref_id      text,
    actor       text,               -- who: user email / phone / 'system'
    note        text,
    created_at  timestamptz not null default now()
);
create index if not exists idx_stock_movements_product
    on commerce_stock_movements (tenant_id, product_id, created_at desc);
create index if not exists idx_stock_movements_ref
    on commerce_stock_movements (tenant_id, ref_type, ref_id);
alter table commerce_stock_movements enable row level security;

-- Set (p_set) or add (p_delta) stock for one product or variant, in one transaction, and record
-- it. Returns the quantity after, or NULL when the row doesn't exist for this tenant. An
-- untracked row (stock_quantity IS NULL) becomes tracked the moment it's counted or received.
create or replace function apply_stock_change(
    p_tenant_id  text,
    p_product_id uuid,
    p_variant_id uuid,
    p_delta      integer,
    p_set        integer,
    p_reason     text,
    p_ref_type   text default null,
    p_ref_id     text default null,
    p_actor      text default null,
    p_note       text default null
) returns integer
language plpgsql set search_path to 'public' as $$
declare
    v_old   integer;
    v_new   integer;
    v_found boolean;
begin
    if p_variant_id is not null then
        select stock_quantity, true into v_old, v_found
        from commerce_product_variants
        where id = p_variant_id and tenant_id = p_tenant_id and product_id = p_product_id
        for update;
    else
        select stock_quantity, true into v_old, v_found
        from commerce_products
        where id = p_product_id and tenant_id = p_tenant_id
        for update;
    end if;
    if not coalesce(v_found, false) then
        return null;
    end if;

    v_new := case when p_set is not null then p_set else coalesce(v_old, 0) + coalesce(p_delta, 0) end;
    v_new := greatest(0, v_new);

    if p_variant_id is not null then
        update commerce_product_variants
        set stock_quantity = v_new, in_stock = v_new > 0, updated_at = now()
        where id = p_variant_id;
    else
        update commerce_products
        set stock_quantity = v_new, in_stock = v_new > 0, updated_at = now()
        where id = p_product_id;
    end if;

    insert into commerce_stock_movements
        (tenant_id, product_id, variant_id, delta, qty_after, reason, ref_type, ref_id, actor, note)
    values (p_tenant_id, p_product_id, p_variant_id, v_new - coalesce(v_old, 0), v_new, p_reason,
            p_ref_type, p_ref_id, p_actor, p_note);
    return v_new;
end;
$$;
