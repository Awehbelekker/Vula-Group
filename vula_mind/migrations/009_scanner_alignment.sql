-- ============================================================
-- Vula Group — Migration 009: Smart Scanner Alignment
-- Align commerce_invoices and commerce_expenses with scanner requirements.
-- Add commerce_suppliers table for payment-terms lookup.
-- Run in: Supabase Dashboard > SQL Editor > New Query > Run
-- Depends on: 006_invoices_broadcasts_expenses.sql
-- ============================================================

-- ── Suppliers ───────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commerce_suppliers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT NOT NULL,
    name                TEXT NOT NULL,
    aliases             TEXT[] NOT NULL DEFAULT '{}',
    payment_terms_days  INTEGER NOT NULL DEFAULT 30,
    category            TEXT DEFAULT 'general',
    contact_phone       TEXT,
    contact_email       TEXT,
    account_number      TEXT,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, name)
);

CREATE INDEX IF NOT EXISTS idx_suppliers_tenant ON commerce_suppliers(tenant_id);

ALTER TABLE commerce_suppliers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "tenant_isolation" ON commerce_suppliers;
CREATE POLICY "tenant_isolation" ON commerce_suppliers
    USING (tenant_id = current_setting('app.tenant_id', TRUE));

DROP TRIGGER IF EXISTS trg_suppliers_updated_at ON commerce_suppliers;
CREATE TRIGGER trg_suppliers_updated_at
    BEFORE UPDATE ON commerce_suppliers
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();


-- ── Invoices (Alignment for Inbound/Scanner) ────────────────────────────────

ALTER TABLE commerce_invoices ADD COLUMN IF NOT EXISTS direction TEXT NOT NULL DEFAULT 'outbound' CHECK (direction IN ('outbound', 'inbound'));
ALTER TABLE commerce_invoices ADD COLUMN IF NOT EXISTS supplier TEXT;
ALTER TABLE commerce_invoices ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE commerce_invoices ADD COLUMN IF NOT EXISTS scan_confidence TEXT;


-- ── Expenses (Alignment for Scanner/Due View) ───────────────────────────────

ALTER TABLE commerce_expenses ADD COLUMN IF NOT EXISTS due_date DATE;
ALTER TABLE commerce_expenses ADD COLUMN IF NOT EXISTS payment_terms_days INTEGER DEFAULT 30;
ALTER TABLE commerce_expenses ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending', 'paid', 'cancelled'));
ALTER TABLE commerce_expenses ADD COLUMN IF NOT EXISTS source TEXT NOT NULL DEFAULT 'manual';
ALTER TABLE commerce_expenses ADD COLUMN IF NOT EXISTS doc_type TEXT;
ALTER TABLE commerce_expenses ADD COLUMN IF NOT EXISTS line_items JSONB NOT NULL DEFAULT '[]';
ALTER TABLE commerce_expenses ADD COLUMN IF NOT EXISTS scan_confidence TEXT;
ALTER TABLE commerce_expenses ADD COLUMN IF NOT EXISTS paid_at TIMESTAMPTZ;
