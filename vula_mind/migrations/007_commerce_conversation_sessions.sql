-- ============================================================
-- Vula Group — Supabase Migration 007: Commerce Conversation Sessions
-- Run in: Supabase Dashboard > SQL Editor > New Query
--
-- Per-tenant multi-turn memory for the commerce_assistant skill.
-- One session per (tenant_id, session_key); session_key is the customer's
-- phone number (WhatsApp) or browser session id (web). Messages persist so
-- context.history survives across webhook invocations and restarts.
-- ============================================================

-- ── Conversation sessions ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commerce_conversation_sessions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL,
    session_key     TEXT NOT NULL,        -- phone number (WhatsApp) or web session id
    channel         TEXT NOT NULL DEFAULT 'whatsapp'
                        CHECK (channel IN ('whatsapp', 'web')),
    customer_phone  TEXT,
    customer_name   TEXT,
    last_skill      TEXT,                 -- last skill that handled this session
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, session_key)
);

CREATE INDEX IF NOT EXISTS idx_conv_sessions_tenant ON commerce_conversation_sessions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_conv_sessions_key    ON commerce_conversation_sessions(tenant_id, session_key);

ALTER TABLE commerce_conversation_sessions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON commerce_conversation_sessions
    USING (tenant_id = current_setting('app.tenant_id', TRUE));

-- ── Conversation messages ─────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commerce_conversation_messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL,
    session_id      UUID NOT NULL
                        REFERENCES commerce_conversation_sessions(id) ON DELETE CASCADE,
    role            TEXT NOT NULL
                        CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content         TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conv_messages_session ON commerce_conversation_messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_conv_messages_tenant  ON commerce_conversation_messages(tenant_id);

ALTER TABLE commerce_conversation_messages ENABLE ROW LEVEL SECURITY;
CREATE POLICY "tenant_isolation" ON commerce_conversation_messages
    USING (tenant_id = current_setting('app.tenant_id', TRUE));

-- ── Trigger: auto-update updated_at on sessions ───────────────────────────────
-- set_updated_at() is defined in migration 002.

CREATE TRIGGER trg_conv_sessions_updated_at
    BEFORE UPDATE ON commerce_conversation_sessions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
