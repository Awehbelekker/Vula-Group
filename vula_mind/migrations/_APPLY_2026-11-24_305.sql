-- Vula: migration 305 in one paste (24 Nov 2026) — coach profiles and the find-a-school listing.
-- Safe to run more than once. Paste ALL of it into an empty Supabase SQL editor and press Run.
-- One transaction: all of it applies, or none of it. Needs migration 303 (already applied).
begin;

-- ===== 305_lesson_directory.sql =====
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

insert into vula_schema_migrations (filename, number, sha256, applied_by, baseline) values ('305_lesson_directory.sql', 305, 'b6fbe0e224497667f60abc56a78421016c7b82d3af6d90a4ae21def0c0cfe29c', 'sql-editor', false) on conflict do nothing;

commit;

select 'done' as result,
       (select count(*) from information_schema.columns where table_schema = 'public' and table_name = 'commerce_lesson_settings' and column_name in ('coach_profiles', 'listing')) as new_columns;
