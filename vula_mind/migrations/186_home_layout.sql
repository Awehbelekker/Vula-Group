-- 186_home_layout.sql — which cards a business sees on its dashboard Home, in its own order.
--
-- 2026-09-29 (Ian): tenants have no way to "customise what they want to view". Home was built
-- for online orders (today's revenue, orders this week), so DIGG — a construction business
-- that works in projects — saw mostly zeros. vula/api/tenants.py GET/PUT /{tenant}/home: an
-- ordered list of card ids; NULL means the default for the business type. Idempotent.

alter table vula_tenant_config add column if not exists home_layout jsonb;
