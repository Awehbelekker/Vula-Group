-- 262_member_team.sql — which team a person is in: sales, admin, bookkeeping… (12 Nov 2026).
--
-- A preset (vula/team_presets.py) gives a new person the dashboard tabs that team's job needs,
-- so each person sees only what they use; the owner can still change any tab after. The team
-- also names a group for "what did the sales team do?", Team › Activity and staff notices.
-- Notifications are not set by a team — each person still chooses their own. Idempotent.

alter table vula_team_members add column if not exists team text;
create index if not exists idx_team_members_team on vula_team_members (tenant_id, team);
