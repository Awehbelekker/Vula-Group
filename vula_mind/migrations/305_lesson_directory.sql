-- 305_lesson_directory.sql — coach profiles and the "find a school near me" listing
-- (vula/commerce/lesson_directory.py, 24 Nov 2026).
--
-- Both live on the business's own lesson settings row, so they carry its tenant_id and its RLS:
--   coach_profiles — what a school chooses to show of each coach: [{member_id, name, bio,
--                    photo_url, levels, sports, shown}]. The name is the team member's first name,
--                    never typed; nothing is shown until the school ticks `shown`.
--   listing        — {listed, about, town}: whether the school asked to be found in the customer
--                    app's directory, with its own one line and town. Never on by default.
--
-- Idempotent. No new table.

alter table commerce_lesson_settings add column if not exists coach_profiles jsonb not null default '[]'::jsonb;
alter table commerce_lesson_settings add column if not exists listing jsonb not null default '{}'::jsonb;
