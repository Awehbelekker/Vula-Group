-- 217_ui_v2_default_on.sql — the redesigned dashboard for every business, new and existing.
--
-- 2026-10-19 (UX redesign, after Phase F moved every screen onto the new components): Ian chose
-- to move everyone over. 215 added vula_tenant_config.ui_v2 as an opt-in (default false); this
-- makes the new design the default for tenants created from now on and switches on every
-- existing tenant. The dashboard still needs its VITE_UI_V2 build flag (on in production).
--
-- Reversible per business: Master › Tenants › (tenant) › "New dashboard design" sets ui_v2 back
-- to false for one tenant (PATCH /v1/master/tenants/{id}, audited); `?ui=v1` previews the old
-- look on one device. Idempotent; no table or column added.

alter table vula_tenant_config alter column ui_v2 set default true;

update vula_tenant_config set ui_v2 = true where ui_v2 is distinct from true;

comment on column vula_tenant_config.ui_v2 is
  'Redesigned dashboard shell for this tenant (needs the VITE_UI_V2 build flag too). Default on since 217; master can switch one tenant back.';
