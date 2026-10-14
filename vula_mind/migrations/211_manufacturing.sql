-- 211_manufacturing.sql — what it actually costs to make something (Manufacturing Core, phase 2–3).
--
-- 2026-10-14 (Vula Manufacturing Core design, Richard; Ian: "let's say we add another tenant — we
-- can't wait to build every time"): one generic costing core any business that makes things can
-- switch on (the "manufacturing" module) — basins, food production, a kitchen. Each business
-- enters its own materials, recipes and batches; nothing here is per-tenant code.
--
--   commerce_materials           raw materials and in-house sub-assemblies, in a base unit (g, ml,
--                                each, m, m2), with the weighted-average cost per base unit
--   commerce_material_movements  every material change: receive, issue to a batch, adjust, count
--   commerce_boms / _lines       recipes, versioned: editing makes a new version, so a past batch
--                                keeps the recipe (and the cost) it was made with
--   commerce_production_runs     one row per batch: recipe version, what went in, good units out,
--                                rejects, hours, and the cost worked out on the day it's confirmed
--   commerce_costing_settings    the business's labour rate and overhead rule (picked once)
--
-- Quantities are numeric(14,4) and costs per base unit numeric(18,6) (migration 210's rule); money
-- totals are integer cents, rounded once. The functions below are the only way materials and
-- batches change: each is one transaction, so a batch either fully happens or doesn't.
-- Nothing is dropped. Idempotent. RLS on every table.

create table if not exists commerce_materials (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    name        text not null,
    base_unit   text not null default 'each' check (base_unit in ('g', 'ml', 'each', 'm', 'm2')),
    pack_size   numeric(14,4),              -- what one purchase pack holds, in pack_unit (25 for a 25 kg bag)
    pack_unit   text,
    unit_cost   numeric(18,6),              -- weighted-average cost per base unit, rands, excl. claimable VAT
    stock_qty   numeric(14,4) not null default 0,   -- base units; may dip below 0 if a batch is logged before the delivery
    supplier    text,
    active      boolean not null default true,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);
create unique index if not exists uq_commerce_materials_name
    on commerce_materials (tenant_id, lower(name)) where active;
alter table commerce_materials enable row level security;

create table if not exists commerce_material_movements (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    material_id     uuid not null references commerce_materials (id),
    delta           numeric(14,4) not null,
    qty_after       numeric(14,4),
    unit_cost       numeric(18,6),          -- what this movement was valued at, per base unit
    unit_cost_after numeric(18,6),          -- the material's average cost after it
    reason          text not null check (reason in ('receive', 'issue', 'adjust', 'count', 'opening', 'reverse')),
    ref_type        text,                   -- 'production_run' | 'delivery' | 'invoice' | 'whatsapp' | 'dashboard'
    ref_id          text,
    actor           text,
    note            text,
    created_at      timestamptz not null default now()
);
create index if not exists idx_material_movements_material
    on commerce_material_movements (tenant_id, material_id, created_at desc);
alter table commerce_material_movements enable row level security;

create table if not exists commerce_boms (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    product_id    uuid,                     -- the product it makes (sold)
    material_id   uuid references commerce_materials (id),   -- or a sub-assembly used in other recipes
    version       integer not null default 1,
    status        text not null default 'active' check (status in ('active', 'archived')),
    batch_yield   numeric(14,4) not null default 1 check (batch_yield > 0),   -- good units one batch should give
    labour_hours  numeric(10,3) not null default 0 check (labour_hours >= 0), -- per batch
    notes         text,
    created_by    text,
    created_at    timestamptz not null default now(),
    check ((product_id is null) <> (material_id is null))
);
create unique index if not exists uq_commerce_boms_active
    on commerce_boms (tenant_id, (coalesce(product_id, material_id))) where status = 'active';
create unique index if not exists uq_commerce_boms_version
    on commerce_boms (tenant_id, (coalesce(product_id, material_id)), version);
alter table commerce_boms enable row level security;

create table if not exists commerce_bom_lines (
    id           uuid primary key default gen_random_uuid(),
    bom_id       uuid not null references commerce_boms (id) on delete cascade,
    tenant_id    text not null,
    material_id  uuid not null references commerce_materials (id),
    qty          numeric(14,4) not null check (qty > 0),     -- as the owner said it: 18
    unit         text not null,                              -- kg
    qty_base     numeric(14,4) not null check (qty_base > 0),-- in the material's base unit: 18000 (g)
    waste_pct    numeric(6,3) not null default 0 check (waste_pct >= 0 and waste_pct < 100),
    position     integer not null default 0
);
create index if not exists idx_bom_lines_bom on commerce_bom_lines (bom_id);
alter table commerce_bom_lines enable row level security;

create table if not exists commerce_production_runs (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    bom_id          uuid not null references commerce_boms (id),
    product_id      uuid,
    material_id     uuid references commerce_materials (id),
    status          text not null default 'draft' check (status in ('draft', 'confirmed', 'reversed')),
    batches         numeric(10,3) not null default 1 check (batches > 0),
    good_qty        numeric(14,4) not null check (good_qty > 0),
    reject_qty      numeric(14,4) not null default 0 check (reject_qty >= 0),
    labour_hours    numeric(10,3) not null default 0 check (labour_hours >= 0),
    inputs          jsonb not null default '[]'::jsonb,   -- [{material_id, qty_base}] — what went in
    labour_cents    bigint not null default 0,
    overhead_cents  bigint not null default 0,
    materials_cents bigint,                 -- set when confirmed, from that day's material costs
    total_cents     bigint,
    unit_cost       numeric(18,6),          -- rands per good unit
    issued          jsonb,                  -- [{material_id, qty_base, unit_cost}] as issued — for a reversal
    actor           text,
    note            text,
    created_at      timestamptz not null default now(),
    confirmed_at    timestamptz,
    reversed_at     timestamptz
);
create index if not exists idx_production_runs_output
    on commerce_production_runs (tenant_id, (coalesce(product_id, material_id)), created_at desc);
alter table commerce_production_runs enable row level security;

create table if not exists commerce_costing_settings (
    tenant_id            text primary key,
    labour_rate_cents    bigint not null default 0 check (labour_rate_cents >= 0),   -- per hour
    overhead_rule        text not null default 'none'
                         check (overhead_rule in ('none', 'per_labour_hour', 'per_run', 'per_unit')),
    overhead_rate_cents  bigint not null default 0 check (overhead_rate_cents >= 0),
    updated_by           text,
    updated_at           timestamptz not null default now()
);
alter table commerce_costing_settings enable row level security;


-- Move a material: set (p_set) or add (p_delta) stock, in base units, and record it. A receipt
-- (or a reversal giving stock back) with a cost updates the weighted-average cost over what's on
-- hand; anything else leaves the average alone. Returns {qty_after, unit_cost_after, valued_at},
-- or NULL when the material isn't this tenant's.
create or replace function material_move(
    p_tenant_id   text,
    p_material_id uuid,
    p_delta       numeric,
    p_set         numeric,
    p_unit_cost   numeric,
    p_reason      text,
    p_ref_type    text default null,
    p_ref_id      text default null,
    p_actor       text default null,
    p_note        text default null
) returns jsonb
language plpgsql set search_path to 'public' as $$
declare
    v_qty    numeric;
    v_cost   numeric;
    v_delta  numeric;
    v_new    numeric;
    v_cost2  numeric;
    v_valued numeric;
begin
    select stock_qty, unit_cost into v_qty, v_cost
      from commerce_materials where id = p_material_id and tenant_id = p_tenant_id for update;
    if not found then
        return null;
    end if;
    v_delta := case when p_set is not null then p_set - v_qty else coalesce(p_delta, 0) end;
    v_new := v_qty + v_delta;
    if p_reason in ('receive', 'reverse', 'opening') and v_delta > 0 and p_unit_cost is not null then
        -- weighted average over what's actually on hand (stock below 0 counts as none)
        v_cost2 := case when v_cost is null or greatest(v_qty, 0) = 0 then p_unit_cost
                        else round((greatest(v_qty, 0) * v_cost + v_delta * p_unit_cost)
                                   / (greatest(v_qty, 0) + v_delta), 6) end;
        v_valued := p_unit_cost;
    else
        v_cost2 := v_cost;
        v_valued := v_cost;
    end if;
    update commerce_materials set stock_qty = v_new, unit_cost = v_cost2, updated_at = now()
     where id = p_material_id;
    insert into commerce_material_movements
        (tenant_id, material_id, delta, qty_after, unit_cost, unit_cost_after, reason, ref_type, ref_id, actor, note)
    values (p_tenant_id, p_material_id, v_delta, v_new, v_valued, v_cost2, p_reason,
            p_ref_type, p_ref_id, p_actor, p_note);
    return jsonb_build_object('qty_after', v_new, 'unit_cost_after', v_cost2, 'valued_at', v_valued);
end;
$$;


-- Save a recipe as a new version (the previous active one is archived, kept for past batches).
-- p_lines: [{material_id, qty, unit, qty_base, waste_pct}]. Returns the new recipe's id.
create or replace function save_bom_version(
    p_tenant_id    text,
    p_product_id   uuid,
    p_material_id  uuid,
    p_batch_yield  numeric,
    p_labour_hours numeric,
    p_lines        jsonb,
    p_actor        text default null,
    p_notes        text default null
) returns uuid
language plpgsql set search_path to 'public' as $$
declare
    v_output  uuid := coalesce(p_product_id, p_material_id);
    v_version integer;
    v_id      uuid;
    v_line    record;
    v_pos     integer := 0;
begin
    if (p_product_id is null) = (p_material_id is null) then
        raise exception 'a recipe makes exactly one product or one material';
    end if;
    if jsonb_array_length(coalesce(p_lines, '[]'::jsonb)) = 0 then
        raise exception 'a recipe needs at least one material';
    end if;
    perform 1 from commerce_boms
     where tenant_id = p_tenant_id and coalesce(product_id, material_id) = v_output for update;
    select coalesce(max(version), 0) + 1 into v_version from commerce_boms
     where tenant_id = p_tenant_id and coalesce(product_id, material_id) = v_output;
    update commerce_boms set status = 'archived'
     where tenant_id = p_tenant_id and coalesce(product_id, material_id) = v_output and status = 'active';
    insert into commerce_boms (tenant_id, product_id, material_id, version, batch_yield, labour_hours, notes, created_by)
    values (p_tenant_id, p_product_id, p_material_id, v_version, p_batch_yield, coalesce(p_labour_hours, 0),
            p_notes, p_actor)
    returning id into v_id;
    for v_line in select * from jsonb_to_recordset(p_lines)
            as x(material_id uuid, qty numeric, unit text, qty_base numeric, waste_pct numeric) loop
        perform 1 from commerce_materials where id = v_line.material_id and tenant_id = p_tenant_id;
        if not found then
            raise exception 'material % is not this business''s', v_line.material_id;
        end if;
        insert into commerce_bom_lines (bom_id, tenant_id, material_id, qty, unit, qty_base, waste_pct, position)
        values (v_id, p_tenant_id, v_line.material_id, v_line.qty, v_line.unit, v_line.qty_base,
                coalesce(v_line.waste_pct, 0), v_pos);
        v_pos := v_pos + 1;
    end loop;
    return v_id;
end;
$$;


-- Confirm a batch: issue every input at its average cost, add the good units to stock at the
-- batch's cost per unit (the product's own average cost updates the same way), and store the
-- figures. One transaction: all of it or none. A material with no cost yet stops it — a batch is
-- never costed on a guess. Returns the figures, or {error}.
create or replace function confirm_production_run(
    p_tenant_id text,
    p_run_id    uuid,
    p_actor     text default null
) returns jsonb
language plpgsql set search_path to 'public' as $$
declare
    r          commerce_production_runs%rowtype;
    v_in       record;
    v_cost     numeric;
    v_qty      numeric;
    v_mat      numeric := 0;
    v_issued   jsonb := '[]'::jsonb;
    v_mat_c    bigint;
    v_total    bigint;
    v_unit     numeric;
    v_fq       numeric;
    v_fc       numeric;
    v_fc2      numeric;
begin
    select * into r from commerce_production_runs where id = p_run_id and tenant_id = p_tenant_id for update;
    if not found then
        return jsonb_build_object('error', 'not_found');
    end if;
    if r.status <> 'draft' then
        return jsonb_build_object('error', 'already_' || r.status);
    end if;
    for v_in in select * from jsonb_to_recordset(r.inputs) as x(material_id uuid, qty_base numeric) loop
        select unit_cost, stock_qty into v_cost, v_qty from commerce_materials
         where id = v_in.material_id and tenant_id = p_tenant_id for update;
        if not found then
            raise exception 'material % is not this business''s', v_in.material_id;
        end if;
        if v_cost is null then
            raise exception 'NO_COST:%', v_in.material_id;
        end if;
        v_mat := v_mat + v_in.qty_base * v_cost;
        update commerce_materials set stock_qty = stock_qty - v_in.qty_base, updated_at = now()
         where id = v_in.material_id;
        insert into commerce_material_movements
            (tenant_id, material_id, delta, qty_after, unit_cost, unit_cost_after, reason, ref_type, ref_id, actor)
        values (p_tenant_id, v_in.material_id, -v_in.qty_base, v_qty - v_in.qty_base, v_cost, v_cost, 'issue',
                'production_run', r.id::text, coalesce(p_actor, r.actor));
        v_issued := v_issued || jsonb_build_object('material_id', v_in.material_id,
                                                   'qty_base', v_in.qty_base, 'unit_cost', v_cost);
    end loop;
    v_mat_c := round(v_mat * 100)::bigint;
    v_total := v_mat_c + r.labour_cents + r.overhead_cents;
    v_unit := round((v_total / 100.0) / r.good_qty, 6);

    if r.product_id is not null then
        select stock_quantity, unit_cost into v_fq, v_fc from commerce_products
         where id = r.product_id and tenant_id = p_tenant_id for update;
        if not found then
            raise exception 'product % is not this business''s', r.product_id;
        end if;
        v_fc2 := case when v_fc is null or coalesce(v_fq, 0) <= 0 then v_unit
                      else round((v_fq * v_fc + r.good_qty * v_unit) / (v_fq + r.good_qty), 6) end;
        update commerce_products set unit_cost = v_fc2, cost_cents = round(v_fc2 * 100)::integer
         where id = r.product_id;
        perform apply_stock_change_exact(p_tenant_id, r.product_id, null, r.good_qty, null, 'receive',
                                         'production_run', r.id::text, coalesce(p_actor, r.actor), 'made');
    else
        perform material_move(p_tenant_id, r.material_id, r.good_qty, null, v_unit, 'receive',
                              'production_run', r.id::text, coalesce(p_actor, r.actor), 'made');
    end if;

    update commerce_production_runs
       set status = 'confirmed', materials_cents = v_mat_c, total_cents = v_total, unit_cost = v_unit,
           issued = v_issued, confirmed_at = now(), actor = coalesce(p_actor, actor)
     where id = r.id;
    return jsonb_build_object('materials_cents', v_mat_c, 'labour_cents', r.labour_cents,
                              'overhead_cents', r.overhead_cents, 'total_cents', v_total,
                              'unit_cost', v_unit, 'good_qty', r.good_qty);
end;
$$;


-- Undo a confirmed batch (a mistake is corrected by reversal and re-entry, never by editing, so
-- the history stays true): materials go back at the cost they left at, the good units come off
-- stock. Returns {reversed: true} or {error}.
create or replace function reverse_production_run(
    p_tenant_id text,
    p_run_id    uuid,
    p_actor     text default null
) returns jsonb
language plpgsql set search_path to 'public' as $$
declare
    r     commerce_production_runs%rowtype;
    v_in  record;
    v_fq  numeric;
    v_fc  numeric;
begin
    select * into r from commerce_production_runs where id = p_run_id and tenant_id = p_tenant_id for update;
    if not found then
        return jsonb_build_object('error', 'not_found');
    end if;
    if r.status <> 'confirmed' then
        return jsonb_build_object('error', 'not_confirmed');
    end if;
    for v_in in select * from jsonb_to_recordset(coalesce(r.issued, '[]'::jsonb))
            as x(material_id uuid, qty_base numeric, unit_cost numeric) loop
        perform material_move(p_tenant_id, v_in.material_id, v_in.qty_base, null, v_in.unit_cost, 'reverse',
                              'production_run', r.id::text, p_actor, 'batch reversed');
    end loop;
    if r.product_id is not null then
        select stock_quantity, unit_cost into v_fq, v_fc from commerce_products
         where id = r.product_id and tenant_id = p_tenant_id for update;
        -- take this batch back out of the product's average cost while some stock remains
        if v_fc is not null and coalesce(v_fq, 0) - r.good_qty > 0 then
            update commerce_products
               set unit_cost = round((v_fq * v_fc - r.good_qty * r.unit_cost) / (v_fq - r.good_qty), 6),
                   cost_cents = round((v_fq * v_fc - r.good_qty * r.unit_cost) / (v_fq - r.good_qty) * 100)::integer
             where id = r.product_id;
        end if;
        perform apply_stock_change_exact(p_tenant_id, r.product_id, null, -r.good_qty, null, 'adjust',
                                         'production_run', r.id::text, p_actor, 'batch reversed');
    else
        perform material_move(p_tenant_id, r.material_id, -r.good_qty, null, null, 'reverse',
                              'production_run', r.id::text, p_actor, 'batch reversed');
    end if;
    update commerce_production_runs set status = 'reversed', reversed_at = now() where id = r.id;
    return jsonb_build_object('reversed', true);
end;
$$;
