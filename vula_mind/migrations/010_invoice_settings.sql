-- ============================================================
-- Vula Group — Migration 010: Invoice Settings (onboarding + look-and-feel)
-- Per-tenant business metadata, EFT banking details, and the chosen
-- invoice PDF template. One row per tenant.
-- Run in: Supabase Dashboard > SQL Editor > New Query > Run
-- Depends on: 006_invoices_broadcasts_expenses.sql
-- ============================================================

CREATE TABLE IF NOT EXISTS commerce_invoice_settings (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT NOT NULL,

    -- Business metadata
    vat_number          TEXT,
    registered_address  TEXT,

    -- EFT banking details (no secrets — these appear on the invoice itself)
    account_name        TEXT,
    bank_name           TEXT,
    branch_code         TEXT,
    account_number      TEXT,

    -- Look and feel
    template_choice     TEXT NOT NULL DEFAULT 'classic'
                            CHECK (template_choice IN ('classic','minimal','modern')),
    accent_color        TEXT,

    -- First-run wizard completion flag
    onboarded           BOOLEAN NOT NULL DEFAULT FALSE,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_invoice_settings_tenant
    ON commerce_invoice_settings(tenant_id);

DROP TRIGGER IF EXISTS trg_invoice_settings_updated_at ON commerce_invoice_settings;
CREATE TRIGGER trg_invoice_settings_updated_at
    BEFORE UPDATE ON commerce_invoice_settings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE commerce_invoice_settings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "tenant_isolation" ON commerce_invoice_settings;
CREATE POLICY "tenant_isolation" ON commerce_invoice_settings
    USING (tenant_id = current_setting('app.tenant_id', TRUE));
