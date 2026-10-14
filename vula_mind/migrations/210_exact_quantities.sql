-- 210_exact_quantities.sql — stock in exact amounts: 2.5 kg, 0.25 l, 12 each.
--
-- 2026-10-14 (Vula Manufacturing Core, phase 1 — "exact units"): stock quantities, stock
-- movements, stock-count lines and the stock functions were all whole numbers, while cart and
-- order quantities were already decimals. So a 0.5 kg sale took 0 off stock and a 1.5 kg sale
-- took 2 (the code rounded to fit), and 2.5 kg of resin couldn't be held at all. Quantities are
-- now numeric(14,4), and the stock functions have exact-amount versions under new names
-- (<name>_exact — same bodies, numeric in and out) that the code calls. The old integer
-- functions are left in place, unused: replacing them needs a DROP, which the hosted migration
-- tool refuses, and leaving them costs nothing. unit_cost is the cost per base unit with
-- sub-cent precision, for costing; cost_cents stays as it is and is copied across.
-- Money totals stay integer cents, computed server-side. Idempotent. RLS unchanged.

alter table commerce_products
    alter column stock_quantity    type numeric(14,4) using stock_quantity::numeric,
    alter column reorder_threshold type numeric(14,4) using reorder_threshold::numeric,
    alter column reorder_qty       type numeric(14,4) using reorder_qty::numeric;
alter table commerce_product_variants
    alter column stock_quantity    type numeric(14,4) using stock_quantity::numeric,
    alter column reorder_threshold type numeric(14,4) using reorder_threshold::numeric,
    alter column reorder_qty       type numeric(14,4) using reorder_qty::numeric;
alter table commerce_stock_movements
    alter column delta     type numeric(14,4) using delta::numeric,
    alter column qty_after type numeric(14,4) using qty_after::numeric;
alter table commerce_stock_count_lines
    alter column counted  type numeric(14,4) using counted::numeric,
    alter column expected type numeric(14,4) using expected::numeric;

alter table commerce_products add column if not exists unit_cost numeric(18,6);
alter table commerce_product_variants add column if not exists unit_cost numeric(18,6);
update commerce_products set unit_cost = cost_cents / 100.0 where unit_cost is null and cost_cents is not null;
update commerce_product_variants set unit_cost = cost_cents / 100.0 where unit_cost is null and cost_cents is not null;

-- The exact-amount stock functions (the code calls these; the integer originals stay, unused).
CREATE OR REPLACE FUNCTION reserve_product_stock_exact(p_tenant_id TEXT, p_product_id UUID, p_qty NUMERIC)
RETURNS BOOLEAN LANGUAGE plpgsql SET search_path TO 'public' AS $$
DECLARE
    v_affected INTEGER;
BEGIN
    UPDATE commerce_products
    SET stock_quantity = CASE WHEN stock_quantity IS NULL THEN NULL ELSE stock_quantity - p_qty END,
        in_stock       = CASE WHEN stock_quantity IS NULL THEN in_stock ELSE stock_quantity - p_qty > 0 END,
        updated_at     = NOW()
    WHERE id = p_product_id
      AND tenant_id = p_tenant_id
      AND (stock_quantity IS NULL OR stock_quantity >= p_qty);
    GET DIAGNOSTICS v_affected = ROW_COUNT;
    RETURN v_affected > 0;
END;
$$;

CREATE OR REPLACE FUNCTION reserve_variant_stock_exact(p_variant_id UUID, p_qty NUMERIC)
RETURNS BOOLEAN LANGUAGE plpgsql SET search_path TO 'public' AS $$
DECLARE
    v_affected INTEGER;
BEGIN
    UPDATE commerce_product_variants
    SET stock_quantity = CASE WHEN stock_quantity IS NULL THEN NULL ELSE stock_quantity - p_qty END,
        in_stock       = CASE WHEN stock_quantity IS NULL THEN in_stock ELSE stock_quantity - p_qty > 0 END,
        updated_at     = NOW()
    WHERE id = p_variant_id
      AND (stock_quantity IS NULL OR stock_quantity >= p_qty);
    GET DIAGNOSTICS v_affected = ROW_COUNT;
    RETURN v_affected > 0;
END;
$$;

CREATE OR REPLACE FUNCTION decrement_product_stock_exact(p_tenant_id TEXT, p_product_id UUID, p_delta NUMERIC)
RETURNS VOID LANGUAGE plpgsql SET search_path TO 'public' AS $$
BEGIN
    UPDATE commerce_products
    SET stock_quantity = CASE WHEN stock_quantity IS NULL THEN NULL ELSE GREATEST(0, stock_quantity - p_delta) END,
        in_stock       = CASE WHEN stock_quantity IS NULL THEN in_stock ELSE stock_quantity - p_delta > 0 END,
        updated_at     = NOW()
    WHERE id = p_product_id AND tenant_id = p_tenant_id;
END;
$$;

CREATE OR REPLACE FUNCTION decrement_variant_stock_exact(p_variant_id UUID, p_delta NUMERIC)
RETURNS VOID LANGUAGE plpgsql SET search_path TO 'public' AS $$
BEGIN
    UPDATE commerce_product_variants
    SET stock_quantity = CASE WHEN stock_quantity IS NULL THEN NULL ELSE GREATEST(0, stock_quantity - p_delta) END,
        in_stock       = CASE WHEN stock_quantity IS NULL THEN in_stock ELSE stock_quantity - p_delta > 0 END,
        updated_at     = NOW()
    WHERE id = p_variant_id;
END;
$$;

create or replace function apply_stock_change_exact(
    p_tenant_id  text,
    p_product_id uuid,
    p_variant_id uuid,
    p_delta      numeric,
    p_set        numeric,
    p_reason     text,
    p_ref_type   text default null,
    p_ref_id     text default null,
    p_actor      text default null,
    p_note       text default null
) returns numeric
language plpgsql set search_path to 'public' as $$
declare
    v_old   numeric;
    v_new   numeric;
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

create or replace function stock_count_scan_exact(
    p_tenant_id  text,
    p_count_id   uuid,
    p_product_id uuid,
    p_variant_id uuid,
    p_add        numeric,
    p_set        numeric,
    p_scan_id    text,
    p_actor      text default null
) returns numeric
language plpgsql set search_path to 'public' as $$
declare
    v_total numeric;
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
