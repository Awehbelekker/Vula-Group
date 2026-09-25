-- 175_vula_tenants_slug_billing.sql — every tenant gets a billing row, keyed by its slug.
--
-- 2026-09-25 review: three tenant-creation paths disagreed. Only the old onboarding wizard wrote
-- vula_tenants (keyed by a random UUID, with the operational slug in workspace_slug); self-serve
-- signup and master "+ New tenant" wrote none, so master's mark-paid / extend-trial / cancel 404'd
-- for them and the billing columns in the Tenants tab were always blank. The app now writes a
-- billing row for every new tenant and looks rows up by workspace_slug. A master-created tenant
-- has no signup contact, so these two columns can no longer be required.
-- Idempotent. vula_tenants already has RLS enabled (migration 001).

alter table vula_tenants alter column contact_name drop not null;
alter table vula_tenants alter column email drop not null;
