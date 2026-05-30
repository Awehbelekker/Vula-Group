-- ============================================================
-- Vula Group — Supabase Migration 003: Per-Tenant WhatsApp Accounts
-- Run in: Supabase Dashboard > SQL Editor > New Query
--
-- Stores one WhatsApp Business account per Vula Commerce tenant.
-- Populated automatically via the Embedded Signup flow in Vula Admin.
-- Ian never needs to touch Meta credentials again after this is set up.
-- ============================================================

CREATE TABLE IF NOT EXISTS vula_whatsapp_accounts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT NOT NULL UNIQUE,
    company_name        TEXT,

    -- Meta identifiers
    waba_id             TEXT,              -- WhatsApp Business Account ID
    phone_number_id     TEXT UNIQUE,       -- Meta phone number ID (e.g. 251439416636328)
    phone_number        TEXT,              -- Human-readable e.g. +27737815979

    -- Credentials (store encrypted in production — use Supabase Vault)
    access_token        TEXT,             -- Meta system user token
    token_type          TEXT DEFAULT 'user'
                            CHECK (token_type IN ('user', 'system')),
    token_expires_at    TIMESTAMPTZ,      -- NULL = never expires (system user token)

    -- Status
    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'connected', 'error', 'disconnected')),
    webhook_registered  BOOLEAN NOT NULL DEFAULT FALSE,
    verified_name       TEXT,             -- Meta-verified display name

    -- Embedded Signup flow tracking
    connected_by        TEXT,             -- user who clicked Connect (email)
    connected_at        TIMESTAMPTZ,
    last_error          TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_whatsapp_accounts_tenant ON vula_whatsapp_accounts(tenant_id);
CREATE INDEX idx_whatsapp_accounts_phone_id ON vula_whatsapp_accounts(phone_number_id);

-- Trigger: auto-update updated_at
CREATE TRIGGER trg_whatsapp_accounts_updated_at
    BEFORE UPDATE ON vula_whatsapp_accounts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- Seed Off the Hook (already manually configured)
INSERT INTO vula_whatsapp_accounts (
    tenant_id, company_name, waba_id, phone_number_id,
    phone_number, status, webhook_registered, verified_name, connected_at
) VALUES (
    'off-the-hook',
    'Off the Hook',
    '1085725605249764',
    '251439416636328',
    '+27737815979',
    'connected',
    TRUE,
    'Off the Hook',
    NOW()
) ON CONFLICT (tenant_id) DO UPDATE SET
    waba_id = EXCLUDED.waba_id,
    phone_number_id = EXCLUDED.phone_number_id,
    phone_number = EXCLUDED.phone_number,
    status = EXCLUDED.status,
    webhook_registered = EXCLUDED.webhook_registered,
    updated_at = NOW();
