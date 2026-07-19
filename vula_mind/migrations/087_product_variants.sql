-- 087_product_variants.sql — Phase 4 of the store-admin-reconciliation plan: product variants
-- with SKU/barcode. Ties into the Smart Scanner becoming a future POS/stock-scanning tool —
-- variants need a real SKU/barcode to scan against.
--
-- Design notes:
-- - price_cents is nullable: a variant with no price of its own inherits the product's own
--   (sale-aware) effective price. A variant WITH its own price_cents is authoritative as-is —
--   sale_price_cents stays a product-level concept (a sale is storewide-per-product for this
--   platform's usage pattern, not per-variant), so it is not re-applied on top of a variant's
--   own price.
-- - reorder_threshold/reorder_qty/default_supplier_id move here so each variant gets its own
--   reorder point (a product-level "Size L is out but Size M has 50 units" can't be expressed
--   with a single product-level threshold once stock is tracked per variant).
--   commerce_products' own reorder_threshold/reorder_qty/default_supplier_id (migration 077)
--   stay in place unchanged and keep working exactly as today for any product with NO variants.
-- - barcode is unique per tenant (partial index, NULLs excluded) so a barcode scan resolves to
--   exactly one variant.

create table if not exists commerce_product_variants (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null,
    product_id uuid not null references commerce_products(id) on delete cascade,
    option_values jsonb not null default '{}'::jsonb,  -- e.g. {"Size": "L", "Colour": "Red"}
    sku text,
    barcode text,
    price_cents integer,           -- null = inherits the product's own effective price
    stock_quantity integer,
    in_stock boolean not null default true,
    sort_order integer not null default 0,
    archived boolean not null default false,
    reorder_threshold integer,
    reorder_qty integer,
    default_supplier_id uuid,       -- matches commerce_products' own field (migration 077): no FK constraint
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_variants_tenant_product
    on commerce_product_variants(tenant_id, product_id);

create unique index if not exists idx_variants_tenant_barcode
    on commerce_product_variants(tenant_id, barcode)
    where barcode is not null;

create trigger trg_commerce_product_variants_updated_at
    before update on commerce_product_variants
    for each row execute function set_updated_at();

alter table commerce_products
    add column if not exists options jsonb;  -- no-op if migration 085 already added it

alter table commerce_cart_items
    add column if not exists variant_id uuid references commerce_product_variants(id);

alter table commerce_order_items
    add column if not exists variant_id uuid references commerce_product_variants(id);

-- Race-safe variant stock decrement (mirrors decrement_product_stock from migration 002).
create or replace function decrement_variant_stock(
    p_variant_id uuid,
    p_delta integer
) returns void as $$
begin
    update commerce_product_variants
    set stock_quantity = greatest(0, coalesce(stock_quantity, 0) - p_delta),
        in_stock = (coalesce(stock_quantity, 0) - p_delta > 0),
        updated_at = now()
    where id = p_variant_id;
end;
$$ language plpgsql;
