-- 166_tenant_spend_cap.sql — optional per-tenant daily LLM spend cap. Go-live readiness audit
-- (2026-09-18) found vula_ai_usage/vula.integrations.metering track cost but nothing enforces
-- it — a tenant (or a bug) could run up cloud LLM spend with no automatic throttle. Opt-in only:
-- null (the default) means uncapped, matching today's behaviour exactly. On breach, generation
-- soft-degrades to local-only (core/llm_router.py) rather than hard-suspending the tenant.
--
-- vula_tenant_config already has RLS enabled (it predates this migration) — no new table, so
-- nothing for tools/check_migrations_rls.py to flag here.

alter table vula_tenant_config
    add column if not exists spend_cap_usd numeric;
