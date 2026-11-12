-- 262_voice_team_and_member_prefs.sql — how Vula sounds, per audience and per person (core/voice.py).
-- 2026-11-12, Ian: replies should fit the company's vibe and the staff member Vula is talking to
-- ("fun, formal, and so on"), without the tone ever changing the facts.
--
--   vula_tenant_config.team_voice_prompt   how Vula talks to the business's OWN people. Separate
--       from persona_prompt (116), which for a shop is the voice learned from its customer
--       messages and was being given to the owner's assistant as well.
--   vula_team_members.prefs                each member's own style, within the company voice:
--       {"style": friendly|fun|formal|direct, "reply_length": short|detailed,
--        "language": <code>, "nickname": <what to call them>}. Fixed choices, validated in
--       core/voice.py::clean_prefs before anything reaches a prompt.
--
-- Idempotent; no new tables (both tables already have RLS enabled).

alter table vula_tenant_config
    add column if not exists team_voice_prompt text;

alter table vula_team_members
    add column if not exists prefs jsonb not null default '{}'::jsonb;
