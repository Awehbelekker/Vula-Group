-- The production tables the manufacturing functions touch, cut down to the columns they use
-- (as in production after migrations 182 and 210). Only for testing migration 211 against a real
-- Postgres; not a migration.
create extension if not exists pgcrypto;
create table commerce_products (
    id uuid primary key default gen_random_uuid(), tenant_id text not null, name text,
    stock_quantity numeric(14,4), in_stock boolean not null default true,
    cost_cents integer, unit_cost numeric(18,6), updated_at timestamptz default now());
create table commerce_product_variants (
    id uuid primary key default gen_random_uuid(), tenant_id text not null, product_id uuid,
    stock_quantity numeric(14,4), in_stock boolean not null default true, updated_at timestamptz default now());
create table commerce_stock_movements (
    id uuid primary key default gen_random_uuid(), tenant_id text not null, product_id uuid not null,
    variant_id uuid, delta numeric(14,4) not null, qty_after numeric(14,4), reason text not null,
    ref_type text, ref_id text, actor text, note text, created_at timestamptz default now());
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


