-- 266_tenant_features.sql — new behaviour switched on for one business before everyone.
--
-- 12 Nov 2026. The switches added this month (composed voice, client memory, private notes,
-- stricter grounding, follow-up routing, the advisor…) were global env settings, so trying one
-- meant trying it on every business at once. A switch is now on for a business when the global
-- setting is on OR this business's own features[name] is true (vula/features.py), so one
-- business can pilot it first. Set by the operator: PATCH /v1/master/tenants/{id}/features.
--
--   features   {"voice_compose": true, "client_memory": true, …} — unknown names are refused by
--              the API; a missing name is off.
--
-- Idempotent; no new tables (vula_tenant_config already has RLS enabled).

alter table vula_tenant_config
    add column if not exists features jsonb not null default '{}'::jsonb;
