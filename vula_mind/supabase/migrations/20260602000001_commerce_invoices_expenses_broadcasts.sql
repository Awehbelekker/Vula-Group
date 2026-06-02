-- Migration 005: Commerce invoices, expenses, and broadcast logs
-- Run AFTER migration 002 (depends on set_updated_at function)

-- ── Invoices / Quotes / Proformas ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commerce_invoices (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT NOT NULL,
    invoice_number      TEXT NOT NULL,
    doc_type            TEXT NOT NULL DEFAULT 'invoice'
                            CHECK (doc_type IN ('invoice', 'quote', 'proforma')),
    customer_name       TEXT NOT NULL,
    customer_email      TEXT,
    customer_phone      TEXT,
    customer_address    TEXT,
    line_items          JSONB NOT NULL DEFAULT '[]',
    subtotal_cents      INTEGER NOT NULL DEFAULT 0,
    vat_rate            NUMERIC(5,2) NOT NULL DEFAULT 15.0,
    vat_cents           INTEGER NOT NULL DEFAULT 0,
    total_cents         INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft','sent','accepted','declined','paid','overdue','cancelled','expired','converted')),
    issue_date          DATE,
    due_date            DATE,
    paid_at             TIMESTAMPTZ,
    order_id            UUID REFERENCES commerce_orders(id) ON DELETE SET NULL,
    converted_to_id     UUID,           -- invoice this quote was converted to
    notes               TEXT,
    payment_method      TEXT,
    yoco_checkout_id    TEXT,
    yoco_payment_id     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(tenant_id, invoice_number)
);

CREATE INDEX IF NOT EXISTS idx_commerce_invoices_tenant  ON commerce_invoices(tenant_id);
CREATE INDEX IF NOT EXISTS idx_commerce_invoices_status  ON commerce_invoices(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_commerce_invoices_phone   ON commerce_invoices(customer_phone);
CREATE INDEX IF NOT EXISTS idx_commerce_invoices_doctype ON commerce_invoices(tenant_id, doc_type);

DROP TRIGGER IF EXISTS trg_commerce_invoices_updated_at ON commerce_invoices;
CREATE TRIGGER trg_commerce_invoices_updated_at
    BEFORE UPDATE ON commerce_invoices
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── Expenses ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commerce_expenses (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL,
    date            DATE NOT NULL,
    category        TEXT NOT NULL DEFAULT 'other'
                        CHECK (category IN (
                            'stock','delivery','packaging','marketing',
                            'equipment','staff','rent','utilities','other'
                        )),
    description     TEXT NOT NULL DEFAULT '',
    amount_cents    INTEGER NOT NULL DEFAULT 0,
    supplier        TEXT,
    receipt_url     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_commerce_expenses_tenant ON commerce_expenses(tenant_id);
CREATE INDEX IF NOT EXISTS idx_commerce_expenses_date   ON commerce_expenses(tenant_id, date);

-- ── Broadcast logs ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commerce_broadcast_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       TEXT NOT NULL,
    name            TEXT NOT NULL,
    template_name   TEXT NOT NULL,
    audience_filter TEXT NOT NULL DEFAULT 'all',
    status          TEXT NOT NULL DEFAULT 'sending'
                        CHECK (status IN ('sending','sent','failed','partial')),
    sent_count      INTEGER NOT NULL DEFAULT 0,
    failed_count    INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_commerce_broadcasts_tenant ON commerce_broadcast_logs(tenant_id);

DROP TRIGGER IF EXISTS trg_commerce_broadcasts_updated_at ON commerce_broadcast_logs;
CREATE TRIGGER trg_commerce_broadcasts_updated_at
    BEFORE UPDATE ON commerce_broadcast_logs
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
