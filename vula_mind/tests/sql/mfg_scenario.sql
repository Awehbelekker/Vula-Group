-- Hand-costed basin batch through migration 211's functions (see tests/test_manufacturing_sql.py).
\set ON_ERROR_STOP 1
insert into commerce_products (id, tenant_id, name) values ('00000000-0000-0000-0000-0000000000b1', 't1', 'Round basin');
insert into commerce_products (id, tenant_id, name) values ('00000000-0000-0000-0000-0000000000b2', 't2', 'Not theirs');
insert into commerce_materials (id, tenant_id, name, base_unit) values
  ('00000000-0000-0000-0000-0000000000c1', 't1', 'Concrete mix', 'g'),
  ('00000000-0000-0000-0000-0000000000c2', 't1', 'Pigment', 'g'),
  ('00000000-0000-0000-0000-0000000000c3', 't1', 'Sealer', 'ml');
-- 2 bags @ R95 (25 kg) then 1 bag @ R100 → weighted average (190 + 100) / 75000 g
select material_move('t1', '00000000-0000-0000-0000-0000000000c1', 50000, null, 0.0038, 'receive') ->> 'unit_cost_after' as concrete_after_1;
select material_move('t1', '00000000-0000-0000-0000-0000000000c1', 25000, null, 0.004, 'receive') ->> 'unit_cost_after' as concrete_after_2;
select material_move('t1', '00000000-0000-0000-0000-0000000000c2', 1000, null, 0.22, 'receive') is not null as pigment;
select material_move('t1', '00000000-0000-0000-0000-0000000000c3', 5000, null, 0.096, 'receive') is not null as sealer;
select material_move('t2', '00000000-0000-0000-0000-0000000000c1', 1, null, 1, 'receive') is null as other_tenant_refused;
-- recipe v1, then v2 (v1 archived, not deleted)
select save_bom_version('t1', '00000000-0000-0000-0000-0000000000b1', null, 4, 6,
  '[{"material_id":"00000000-0000-0000-0000-0000000000c1","qty":70,"unit":"kg","qty_base":70000,"waste_pct":5}]') is not null as v1;
select save_bom_version('t1', '00000000-0000-0000-0000-0000000000b1', null, 4, 6,
  '[{"material_id":"00000000-0000-0000-0000-0000000000c1","qty":72,"unit":"kg","qty_base":72000,"waste_pct":5},
    {"material_id":"00000000-0000-0000-0000-0000000000c2","qty":600,"unit":"g","qty_base":600,"waste_pct":0},
    {"material_id":"00000000-0000-0000-0000-0000000000c3","qty":1,"unit":"l","qty_base":1000,"waste_pct":0}]') is not null as v2;
select string_agg(version || ':' || status, ',' order by version) as versions from commerce_boms;
-- a batch: 3 good, 1 reject, 6 h @ R60 (36000c), overhead R25/h (15000c)
insert into commerce_production_runs (id, tenant_id, bom_id, product_id, good_qty, reject_qty, labour_hours,
  inputs, labour_cents, overhead_cents)
select '00000000-0000-0000-0000-0000000000d1', 't1', id, product_id, 3, 1, 6,
  '[{"material_id":"00000000-0000-0000-0000-0000000000c1","qty_base":75600},
    {"material_id":"00000000-0000-0000-0000-0000000000c2","qty_base":600},
    {"material_id":"00000000-0000-0000-0000-0000000000c3","qty_base":1000}]', 36000, 15000
from commerce_boms where status = 'active';
select confirm_production_run('t1', '00000000-0000-0000-0000-0000000000d1', 'ian') as confirmed;
select confirm_production_run('t1', '00000000-0000-0000-0000-0000000000d1', 'ian') ->> 'error' as second_confirm;
select confirm_production_run('t2', '00000000-0000-0000-0000-0000000000d1', 'x') ->> 'error' as other_tenant;
select stock_quantity, unit_cost, cost_cents from commerce_products where name = 'Round basin';
select name, stock_qty, unit_cost from commerce_materials order by name;
select count(*) as issue_moves from commerce_material_movements where reason = 'issue';
select reverse_production_run('t1', '00000000-0000-0000-0000-0000000000d1', 'ian') as reversed;
select stock_quantity from commerce_products where name = 'Round basin';
select name, stock_qty, unit_cost from commerce_materials order by name;
select status from commerce_production_runs;
