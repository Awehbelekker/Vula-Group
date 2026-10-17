-- 215_tenant_ui_v2.sql — per-tenant opt-in for the redesigned dashboard, and its own labels.
--
-- 2026-10-17 (UX redesign, docs/ux-redesign-audit.md): the new shell and Home ship behind the
-- VITE_UI_V2 build flag AND a per-tenant switch, so one business can move to the new layout
-- while every other keeps the current one. Ian chose opt-in per tenant (not everyone at once).
--
--   ui_v2      true = this tenant sees the new shell when the build has VITE_UI_V2 on.
--              Default false: nothing changes for anyone until a tenant is switched on.
--   ui_labels  the business's own names for sections and Home figures, e.g.
--              {"sections": {"people": "Care", "bookings": "Visits"}, "home_kpis": ["new_leads", ...]}.
--              null = the defaults for the business type. Never changes a section id (deep links
--              and team access lists use the ids), only what it is called.
--
-- Columns on vula_tenant_config, next to home_layout (186); RLS is already on (040).
-- Idempotent; nothing dropped.

alter table vula_tenant_config add column if not exists ui_v2 boolean not null default false;
alter table vula_tenant_config add column if not exists ui_labels jsonb;

comment on column vula_tenant_config.ui_v2 is
  'Redesigned dashboard shell for this tenant (needs the VITE_UI_V2 build flag too). Default off.';
comment on column vula_tenant_config.ui_labels is
  'Tenant''s own section/KPI labels for the redesigned shell; null = business-type defaults.';
