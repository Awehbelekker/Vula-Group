-- Migration 003: Per-Tenant WhatsApp Accounts
CREATE TABLE IF NOT EXISTS vula_whatsapp_accounts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT NOT NULL UNIQUE,
    company_name        TEXT,
    waba_id             TEXT,
    phone_number_id     TEXT UNIQUE,
    phone_number        TEXT,
    access_token        TEXT,
    token_type          TEXT DEFAULT 'user' CHECK (token_type IN ('user', 'system')),
    token_expires_at    TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'connected', 'error', 'disconnected')),
    webhook_registered  BOOLEAN NOT NULL DEFAULT FALSE,
    verified_name       TEXT,
    connected_by        TEXT,
    connected_at        TIMESTAMPTZ,
    last_error          TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_whatsapp_accounts_tenant ON vula_whatsapp_accounts(tenant_id);
CREATE INDEX IF NOT EXISTS idx_whatsapp_accounts_phone_id ON vula_whatsapp_accounts(phone_number_id);

DROP TRIGGER IF EXISTS trg_whatsapp_accounts_updated_at ON vula_whatsapp_accounts;
CREATE TRIGGER trg_whatsapp_accounts_updated_at
    BEFORE UPDATE ON vula_whatsapp_accounts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

INSERT INTO vula_whatsapp_accounts (
    tenant_id, company_name, waba_id, phone_number_id,
    phone_number, status, webhook_registered, verified_name, connected_at
) VALUES (
    'off-the-hook', 'Off the Hook', '1085725605249764', '251439416636328',
    '+27737815979', 'connected', TRUE, 'Off the Hook', NOW()
) ON CONFLICT (tenant_id) DO UPDATE SET
    waba_id = EXCLUDED.waba_id,
    phone_number_id = EXCLUDED.phone_number_id,
    phone_number = EXCLUDED.phone_number,
    status = EXCLUDED.status,
    webhook_registered = EXCLUDED.webhook_registered,
    updated_at = NOW();

-- Migration 004: Per-Tenant Yoco Accounts
CREATE TABLE IF NOT EXISTS vula_yoco_accounts (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT NOT NULL UNIQUE,
    company_name        TEXT,
    secret_key          TEXT NOT NULL,
    public_key          TEXT,
    webhook_secret      TEXT,
    mode                TEXT NOT NULL DEFAULT 'live' CHECK (mode IN ('live', 'test')),
    status              TEXT NOT NULL DEFAULT 'connected'
                            CHECK (status IN ('connected', 'error', 'disconnected')),
    webhook_registered  BOOLEAN NOT NULL DEFAULT FALSE,
    webhook_id          TEXT,
    connected_by        TEXT,
    connected_at        TIMESTAMPTZ DEFAULT NOW(),
    last_error          TEXT,
    last_test_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_yoco_accounts_tenant ON vula_yoco_accounts(tenant_id);

DROP TRIGGER IF EXISTS trg_yoco_accounts_updated_at ON vula_yoco_accounts;
CREATE TRIGGER trg_yoco_accounts_updated_at
    BEFORE UPDATE ON vula_yoco_accounts
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE vula_yoco_accounts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "tenant_isolation" ON vula_yoco_accounts;
CREATE POLICY "tenant_isolation" ON vula_yoco_accounts
    USING (tenant_id = current_setting('app.tenant_id', TRUE));
