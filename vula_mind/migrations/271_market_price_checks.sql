-- 271_market_price_checks.sql — where a market price was read, and whether it checked out.
--
-- 14 Nov 2026 (trade costing, phase 1). The weekly supplier scrape kept whatever the model read
-- (Cashbuild face bricks at R0.35–R0.55 each, a different figure every run). A price is now kept
-- only when its figures are printed on the supplier's page and sit in a plausible band for what
-- it is (vula/takeoff/market_checks.py). Rows from before stay, with checked = false, and are
-- never used to price anything (vula/takeoff/market.py reads checked rows only).
--
-- Market prices are shared reference data, never a business's own (those are in
-- vula_price_observations). Idempotent.

alter table vula_material_rates add column if not exists url text;
alter table vula_material_rates add column if not exists family text;
alter table vula_material_rates add column if not exists checked boolean not null default false;

create index if not exists vula_material_rates_checked_idx on vula_material_rates (checked, scraped_at);

alter table vula_material_rates enable row level security;
