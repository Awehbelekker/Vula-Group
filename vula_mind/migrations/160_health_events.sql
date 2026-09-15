-- 160_health_events.sql — Mass Mind systemic health watch (Phase 1).
--
-- vula/api/server.py's _stale_escalation_scheduler_loop and _stale_handoff_scheduler_loop each
-- recover ONE tenant's stuck conversation independently, with zero visibility into whether the
-- SAME thing is happening across many tenants at once — which is exactly the signal that says a
-- skill or tool is broken platform-wide, not that one tenant got unlucky.
--
-- Checked before writing this: neither loop left any durable trace that a recovery had fired.
-- mark_stale_notified/mark_customer_notified stamp timestamps onto the SAME vula_escalations
-- row (useful for that one escalation, not for counting incidents over time), and
-- set_session_paused just flips a bool back to false with no record an auto-resume happened at
-- all versus the owner replying normally. There was nothing to roll up. This table is that
-- record: one row per recovery, append-only, shared across both loops (and any future health
-- signal) so a rollup can ask "how many DISTINCT tenants did this happen to recently" — the
-- actual "broken thing, not bad luck" test — see core/mass_mind/health.py.

create table if not exists vula_health_events (
    id          bigint generated always as identity primary key,
    tenant_id   text not null,
    kind        text not null,
    detail      jsonb,
    created_at  timestamptz not null default now()
);

-- systemic_incidents()'s real query shape: all events of a kind within a rolling window.
create index if not exists vula_health_events_rollup_idx
    on vula_health_events (kind, created_at desc);

alter table vula_health_events enable row level security;

-- Backend-only table — never read or written by a tenant's own dashboard session, same
-- pattern as vula_reflections (migration 159) / commerce_merchant_profiles (migration 154).
drop policy if exists vula_health_events_service on vula_health_events;
create policy vula_health_events_service on vula_health_events
    for all to service_role using (true) with check (true);
