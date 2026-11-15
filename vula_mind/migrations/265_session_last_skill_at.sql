-- 265_session_last_skill_at.sql — when a conversation's last skill answered (vula/route_memory.py).
--
-- 12 Nov 2026. Routing looks at one message at a time, so a follow-up ("and for next week?")
-- lost its thread. The skill that answered each person's last question was kept in a dict in the
-- web process: a restart emptied it and the other worker never saw it.
-- commerce_conversation_sessions.last_skill has existed since 007 and was never written or read.
-- This adds the time it was set, so "the last skill" can expire after 30 minutes like the
-- in-memory one. Only the skill's NAME is stored, never the question.
--
-- Idempotent; no new tables.

alter table commerce_conversation_sessions
    add column if not exists last_skill_at timestamptz;
