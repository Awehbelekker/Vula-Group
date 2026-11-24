-- 306_staff_targets.sql — the target a team member sets for themselves (vula/tap/scoreboard.py, 24 Nov 2026).
--
-- The staff app's home shows what a person has earned against a target of their own. It was kept
-- on the phone only, so a new phone lost it. One row per person: a day or a week target in cents.
-- It is theirs alone: read and written only for the signed-in person, never shown to anyone else.
--
-- Idempotent. RLS on.

create table if not exists kb_staff_targets (
    tenant_id   text not null,
    member_id   text not null,
    period      text not null default 'day' check (period in ('day', 'week')),
    cents       integer not null check (cents > 0),
    updated_at  timestamptz not null default now(),
    primary key (tenant_id, member_id)
);

alter table kb_staff_targets enable row level security;
drop policy if exists "tenant_isolation" on kb_staff_targets;
create policy "tenant_isolation" on kb_staff_targets
    using (tenant_id = current_setting('app.tenant_id', true));

comment on table kb_staff_targets is
    'A team member''s own earnings target (day or week). Private to that person.';
