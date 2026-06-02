-- ═══════════════════════════════════════════════════════════════════════════
-- Vula Commerce — Admin tables: invoices, expenses, suppliers
-- Run in Supabase SQL Editor
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Supplier registry ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commerce_suppliers (
    id                  TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id           TEXT NOT NULL,
    name                TEXT NOT NULL,
    aliases             TEXT[] DEFAULT '{}',
    payment_terms_days  INTEGER DEFAULT 30,
    category            TEXT DEFAULT 'general',
    contact_phone       TEXT,
    contact_email       TEXT,
    account_number      TEXT,
    notes               TEXT,
    created_at          TIMESTAMPTZ DEFAULT now(),
    UNIQUE(tenant_id, name)
);
CREATE INDEX IF NOT EXISTS idx_suppliers_tenant ON commerce_suppliers(tenant_id);

-- ── Invoices (sales/purchase) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS commerce_invoices (
    id              TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id       TEXT NOT NULL,
    invoice_number  TEXT,
    doc_type        TEXT DEFAULT 'invoice',     -- invoice | quote | proforma | purchase
    direction       TEXT DEFAULT 'outbound',    -- outbound (sale) | inbound (purchase/payable)
    status          TEXT DEFAULT 'draft',       -- draft | sent | paid | overdue | cancelled
    customer        TEXT,
    supplier        TEXT,
    date            DATE DEFAULT CURRENT_DATE,
    due_date        DATE,
    payment_terms_days INTEGER DEFAULT 30,
    subtotal_cents  INTEGER DEFAULT 0,
    vat_cents       INTEGER DEFAULT 0,
    total_cents     INTEGER DEFAULT 0,
    line_items      JSONB DEFAULT '[]',
    notes           TEXT,
    source          TEXT DEFAULT 'manual',      -- manual | scanner | whatsapp
    scan_confidence TEXT,
    paid_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_invoices_tenant  ON commerce_invoices(tenant_id);
CREATE INDEX IF NOT EXISTS idx_invoices_due     ON commerce_invoices(tenant_id, due_date);
CREATE INDEX IF NOT EXISTS idx_invoices_status  ON commerce_invoices(tenant_id, status);

-- ── Expenses (cash / petty cash spend) ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS commerce_expenses (
    id                  TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    tenant_id           TEXT NOT NULL,
    date                DATE DEFAULT CURRENT_DATE,
    due_date            DATE,
    category            TEXT DEFAULT 'other',
    description         TEXT NOT NULL DEFAULT '',
    amount_cents        INTEGER DEFAULT 0,
    supplier            TEXT,
    payment_terms_days  INTEGER,
    status              TEXT DEFAULT 'pending', -- pending | paid | overdue
    source              TEXT DEFAULT 'manual',  -- manual | scanner | whatsapp
    doc_type            TEXT,
    line_items          JSONB DEFAULT '[]',
    scan_confidence     TEXT,
    receipt_url         TEXT,
    paid_at             TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_expenses_tenant ON commerce_expenses(tenant_id);
CREATE INDEX IF NOT EXISTS idx_expenses_due    ON commerce_expenses(tenant_id, due_date);
CREATE INDEX IF NOT EXISTS idx_expenses_status ON commerce_expenses(tenant_id, status);

-- ── Due payments view ─────────────────────────────────────────────────────────
CREATE OR REPLACE VIEW commerce_payables_due AS
SELECT
    id, tenant_id, date, due_date, description, amount_cents, supplier, status,
    source, doc_type, 'expense' AS record_type,
    CASE
        WHEN due_date < CURRENT_DATE THEN 'overdue'
        WHEN due_date <= CURRENT_DATE + 7 THEN 'due_soon'
        ELSE 'upcoming'
    END AS urgency,
    CURRENT_DATE - due_date AS days_overdue
FROM commerce_expenses
WHERE status = 'pending' AND due_date IS NOT NULL
UNION ALL
SELECT
    id, tenant_id, date, due_date, supplier AS description,
    total_cents AS amount_cents, supplier, status,
    source, doc_type, 'invoice' AS record_type,
    CASE
        WHEN due_date < CURRENT_DATE THEN 'overdue'
        WHEN due_date <= CURRENT_DATE + 7 THEN 'due_soon'
        ELSE 'upcoming'
    END AS urgency,
    CURRENT_DATE - due_date AS days_overdue
FROM commerce_invoices
WHERE direction = 'inbound' AND status IN ('sent','draft') AND due_date IS NOT NULL
ORDER BY due_date ASC;

-- ── Seed Off the Hook suppliers ───────────────────────────────────────────────
INSERT INTO commerce_suppliers (tenant_id, name, aliases, payment_terms_days, category)
VALUES
    ('off-the-hook', 'Sea Harvest',           ARRAY['Sea Harvest Pty Ltd'],          30, 'food'),
    ('off-the-hook', 'Oceana Group',          ARRAY['Oceana', 'Lucky Star'],         30, 'food'),
    ('off-the-hook', 'I&J',                   ARRAY['I & J', 'I&J Frozen'],          30, 'food'),
    ('off-the-hook', 'Cape Town Cold Store',  ARRAY['CTCS'],                         30, 'logistics'),
    ('off-the-hook', 'Fish Hoek Fish Market', ARRAY['FHFM'],                          0, 'food')
ON CONFLICT (tenant_id, name) DO NOTHING;
