-- 183_stock_counts.sql — stock-takes: count with the phone camera, review the variance, apply.
--
-- 2026-09-27 (barcode stock counting): a stock-take is a count session. Staff scan items into
-- it (each scan adds one, or a typed quantity is set); the owner or a manager reviews counted
-- against what the system holds and applies it, which posts one 'count' movement per line
-- through apply_stock_change (migration 182). Nothing touches stock until apply.
--
-- Scans can be queued on a phone that's offline and sent later, possibly twice if a reply was
-- lost — each carries a client-made scan_id and stock_count_scan() ignores one it has seen,
-- so an increment is never counted twice. Idempotent.

create table if not exists commerce_stock_counts (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    status      text not null default 'open' check (status in ('open', 'applied', 'cancelled')),
    note        text,
    started_by  text,
    applied_by  text,
    created_at  timestamptz not null default now(),
    applied_at  timestamptz
);
create index if not exists idx_stock_counts_tenant on commerce_stock_counts (tenant_id, created_at desc);
alter table commerce_stock_counts enable row level security;

create table if not exists commerce_stock_count_lines (
    id          uuid primary key default gen_random_uuid(),
    count_id    uuid not null references commerce_stock_counts (id) on delete cascade,
    tenant_id   text not null,
    product_id  uuid not null,
    variant_id  uuid,
    counted     integer not null default 0,
    expected    integer,            -- what the system held, recorded when the count is applied
    counted_by  text,
    updated_at  timestamptz not null default now()
);
create unique index if not exists idx_stock_count_lines_item
    on commerce_stock_count_lines (count_id, product_id,
                                   (coalesce(variant_id, '00000000-0000-0000-0000-000000000000'::uuid)));
alter table commerce_stock_count_lines enable row level security;

create table if not exists commerce_stock_count_scans (
    count_id    uuid not null references commerce_stock_counts (id) on delete cascade,
    scan_id     text not null,
    created_at  timestamptz not null default now(),
    primary key (count_id, scan_id)
);
alter table commerce_stock_count_scans enable row level security;

-- Add (p_add) to or set (p_set) one item's count in an open stock-take. Returns the item's
-- counted total, or NULL when the count isn't open / isn't this tenant's, or the product
-- isn't this tenant's. A repeated p_scan_id changes nothing and returns the current total.
create or replace function stock_count_scan(
    p_tenant_id  text,
    p_count_id   uuid,
    p_product_id uuid,
    p_variant_id uuid,
    p_add        integer,
    p_set        integer,
    p_scan_id    text,
    p_actor      text default null
) returns integer
language plpgsql set search_path to 'public' as $$
declare
    v_total integer;
    v_zero  constant uuid := '00000000-0000-0000-0000-000000000000';
begin
    perform 1 from commerce_stock_counts
     where id = p_count_id and tenant_id = p_tenant_id and status = 'open';
    if not found then
        return null;
    end if;
    perform 1 from commerce_products where id = p_product_id and tenant_id = p_tenant_id;
    if not found then
        return null;
    end if;

    if p_scan_id is not null then
        insert into commerce_stock_count_scans (count_id, scan_id) values (p_count_id, p_scan_id)
        on conflict do nothing;
        if not found then       -- seen before: report the total, change nothing
            select counted into v_total from commerce_stock_count_lines
             where count_id = p_count_id and product_id = p_product_id
               and coalesce(variant_id, v_zero) = coalesce(p_variant_id, v_zero);
            return coalesce(v_total, 0);
        end if;
    end if;

    insert into commerce_stock_count_lines (count_id, tenant_id, product_id, variant_id, counted, counted_by)
    values (p_count_id, p_tenant_id, p_product_id, p_variant_id,
            greatest(0, coalesce(p_set, p_add, 1)), p_actor)
    on conflict (count_id, product_id, (coalesce(variant_id, '00000000-0000-0000-0000-000000000000'::uuid)))
    do update set
        counted    = greatest(0, case when p_set is not null then p_set
                                      else commerce_stock_count_lines.counted + coalesce(p_add, 1) end),
        counted_by = coalesce(p_actor, commerce_stock_count_lines.counted_by),
        updated_at = now()
    returning counted into v_total;
    return v_total;
end;
$$;
