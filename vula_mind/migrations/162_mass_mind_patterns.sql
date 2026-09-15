-- 162_mass_mind_patterns.sql — Mass Mind pattern library (Phase 1, completing the design doc).
--
-- core/memory/reflection.py (migration 159) is per-tenant, fenced — correctly so, it carries
-- goal text and outcome scores. This table is the deliberate SEPARATE aggregation the Mass Mind
-- design doc calls for: no tenant_id, no goal text, no customer content at all — just
-- (business_type, skill, model_tier) -> how often that combination produced a good outcome,
-- platform-wide. business_type is the real, already-established industry-vertical taxonomy
-- (vula_tenant_config.business_type — food/retail/services/trades/health/other, set at
-- onboarding via vula/api/onboarding.py::_map_business_type), not a new one invented for this.
--
-- Consulted only as a fallback (core/hrm/orchestrator.py::_select_model) when a tenant's own
-- reflection history has nothing to say for the current request — never overrides a tenant's
-- own signal. This is what makes a brand-new tenant's routing sensible from message one instead
-- of guessing from a static complexity table, without that tenant's own data ever leaving its
-- fence to make it happen.

create table if not exists vula_mass_mind_patterns (
    id             bigint generated always as identity primary key,
    business_type  text not null,
    skill          text not null,
    model_tier     text not null,
    sample_count   integer not null,
    win_rate       double precision not null,
    avg_score      double precision not null,
    updated_at     timestamptz not null default now(),
    unique (business_type, skill, model_tier)
);

-- suggest_tier()'s real query shape: all tiers for one (business_type, skill) pair.
create index if not exists vula_mass_mind_patterns_lookup_idx
    on vula_mass_mind_patterns (business_type, skill);

alter table vula_mass_mind_patterns enable row level security;

-- Backend-only table, same pattern as vula_reflections / vula_health_events. Nothing here is
-- tenant-identifying, so it would be SAFE to let tenants read it later (a "what Vula has
-- learned platform-wide" transparency feature) — kept service-role-only for now since nothing
-- reads it but the rollup job and the cold-start consult; loosen deliberately if that's built.
drop policy if exists vula_mass_mind_patterns_service on vula_mass_mind_patterns;
create policy vula_mass_mind_patterns_service on vula_mass_mind_patterns
    for all to service_role using (true) with check (true);
