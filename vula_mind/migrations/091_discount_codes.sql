-- 091_discount_codes.sql — customer-facing storefront discount/promo codes. Distinct from the
-- existing invoice-level discount_pct (migrations 053/041, B2B quotes/invoices, staff-entered) —
-- this is the deferred item from the store-admin-reconciliation plan: a code a customer types
-- at checkout. Scoped to storewide percent/fixed/free-shipping for v1 (no per-product/category
-- targeting, no BOGO — those add real complexity with no concrete tenant need yet; easy to add
-- as extra columns later without breaking this).

create table if not exists commerce_discount_codes (
    id uuid primary key default gen_random_uuid(),
    tenant_id text not null,
    code text not null,                                  -- stored as typed; matched case-insensitively
    type text not null check (type in ('percent', 'fixed', 'free_shipping')),
    value integer not null default 0,                     -- percent (1-100) or fixed cents; unused for free_shipping
    min_order_cents integer,                              -- null = no minimum
    starts_at timestamptz,                                -- null = active immediately
    ends_at timestamptz,                                  -- null = no expiry
    usage_limit integer,                                  -- null = unlimited
    usage_count integer not null default 0,
    active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists idx_discount_codes_tenant_code
    on commerce_discount_codes(tenant_id, upper(code));

create trigger trg_commerce_discount_codes_updated_at
    before update on commerce_discount_codes
    for each row execute function set_updated_at();

alter table commerce_orders
    add column if not exists discount_code text,
    add column if not exists discount_cents integer not null default 0;

-- RLS to match every other tenant-scoped table's convention (see migration 090).
alter table commerce_discount_codes enable row level security;
drop policy if exists "tenant_isolation" on commerce_discount_codes;
create policy "tenant_isolation" on commerce_discount_codes
    using (tenant_id = current_setting('app.tenant_id', true));

-- Race-safe usage increment (mirrors decrement_product_stock's pattern) — a raw
-- read-then-write from the app would undercount usage under concurrent checkouts.
create or replace function increment_discount_code_usage(p_code_id uuid)
returns void as $$
begin
    update commerce_discount_codes
    set usage_count = usage_count + 1, updated_at = now()
    where id = p_code_id;
end;
$$ language plpgsql;
