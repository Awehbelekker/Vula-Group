-- ============================================================
-- Vula Group — Migration 048: Shared Inbox handoff support
-- Run in: Supabase Dashboard > SQL Editor > New Query
--
-- Adds human-handoff state + last-message snapshot to conversation
-- sessions so the shared inbox can show the conversation list and
-- the bot can respect a "human took over" flag.
-- ============================================================

-- ── Handoff flag ────────────────────────────────────────────────────────────
ALTER TABLE commerce_conversation_sessions
    ADD COLUMN IF NOT EXISTS paused         BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS paused_by      TEXT,                -- agent phone / user id
    ADD COLUMN IF NOT EXISTS paused_at      TIMESTAMPTZ;

-- ── Last-message snapshot (avoids a JOIN on every list render) ──────────────
ALTER TABLE commerce_conversation_sessions
    ADD COLUMN IF NOT EXISTS last_message   TEXT,
    ADD COLUMN IF NOT EXISTS last_role      TEXT,        -- user | assistant | agent
    ADD COLUMN IF NOT EXISTS last_at        TIMESTAMPTZ;

-- ── Allow 'agent' role in conversation messages (human reply via inbox) ─────
-- Supabase doesn't support ALTER CONSTRAINT directly; drop + recreate.
ALTER TABLE commerce_conversation_messages
    DROP CONSTRAINT IF EXISTS commerce_conversation_messages_role_check;

ALTER TABLE commerce_conversation_messages
    ADD CONSTRAINT commerce_conversation_messages_role_check
        CHECK (role IN ('user', 'assistant', 'system', 'tool', 'agent'));

-- ── Index for paused-session queries ────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_conv_sessions_paused
    ON commerce_conversation_sessions(tenant_id, paused);
