-- ============================================================
-- Vula Group — Migration 006: Invoices, Broadcasts, Expenses
-- Run in: Supabase Dashboard > SQL Editor > New Query > Run
-- ============================================================

-- ── Invoices ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commerce_invoices (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT NOT NULL,
    invoice_number      TEXT NOT NULL,            -- e.g. OTH-0001

    -- Customer
    customer_name       TEXT NOT NULL,
    customer_email      TEXT,
    customer_phone      TEXT,
    customer_address    TEXT,

    -- Line items stored as JSONB array:
    -- [{ product_id, description, qty, unit_price_cents, total_cents }]
    line_items          JSONB NOT NULL DEFAULT '[]',

    -- Totals (cents)
    subtotal_cents      INTEGER NOT NULL DEFAULT 0,
    vat_rate            NUMERIC(5,2) NOT NULL DEFAULT 15.00,  -- 15% VAT
    vat_cents           INTEGER NOT NULL DEFAULT 0,
    total_cents         INTEGER NOT NULL DEFAULT 0,

    -- Status
    status              TEXT NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft','sent','paid','overdue','cancelled')),

    -- Dates
    issue_date          DATE NOT NULL DEFAULT CURRENT_DATE,
    due_date            DATE,
    paid_at             TIMESTAMPTZ,

    -- Linked order (optional)
    order_id            UUID REFERENCES commerce_orders(id),

    -- Payment
    yoco_checkout_id    TEXT,
    yoco_payment_id     TEXT,
    payment_method      TEXT,                     -- 'yoco' | 'eft' | 'cash'

    -- Notes
    notes               TEXT,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE(tenant_id, invoice_number)
);

CREATE INDEX IF NOT EXISTS idx_invoices_tenant     ON commerce_invoices(tenant_id);
CREATE INDEX IF NOT EXISTS idx_invoices_status     ON commerce_invoices(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_invoices_customer   ON commerce_invoices(customer_phone);

DROP TRIGGER IF EXISTS trg_invoices_updated_at ON commerce_invoices;
CREATE TRIGGER trg_invoices_updated_at
    BEFORE UPDATE ON commerce_invoices
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE commerce_invoices ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "tenant_isolation" ON commerce_invoices;
CREATE POLICY "tenant_isolation" ON commerce_invoices
    USING (tenant_id = current_setting('app.tenant_id', TRUE));


-- ── Broadcast logs ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commerce_broadcast_logs (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT NOT NULL,
    name                TEXT NOT NULL,            -- campaign name
    template_name       TEXT NOT NULL,            -- Meta template name
    audience_filter     TEXT NOT NULL DEFAULT 'all',  -- 'all' | 'ordered_30d' | 'high_value'
    recipient_count     INTEGER NOT NULL DEFAULT 0,
    sent_count          INTEGER NOT NULL DEFAULT 0,
    delivered_count     INTEGER NOT NULL DEFAULT 0,
    read_count          INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft','sending','sent','failed')),
    scheduled_at        TIMESTAMPTZ,
    sent_at             TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_broadcasts_tenant ON commerce_broadcast_logs(tenant_id);

ALTER TABLE commerce_broadcast_logs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "tenant_isolation" ON commerce_broadcast_logs;
CREATE POLICY "tenant_isolation" ON commerce_broadcast_logs
    USING (tenant_id = current_setting('app.tenant_id', TRUE));


-- ── Expenses ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS commerce_expenses (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           TEXT NOT NULL,
    date                DATE NOT NULL DEFAULT CURRENT_DATE,
    category            TEXT NOT NULL DEFAULT 'other'
                            CHECK (category IN ('stock','delivery','packaging','marketing','equipment','staff','rent','utilities','other')),
    description         TEXT NOT NULL,
    amount_cents        INTEGER NOT NULL CHECK (amount_cents > 0),
    supplier            TEXT,
    receipt_url         TEXT,                     -- Supabase Storage URL
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_expenses_tenant ON commerce_expenses(tenant_id);
CREATE INDEX IF NOT EXISTS idx_expenses_date   ON commerce_expenses(tenant_id, date);

ALTER TABLE commerce_expenses ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "tenant_isolation" ON commerce_expenses;
CREATE POLICY "tenant_isolation" ON commerce_expenses
    USING (tenant_id = current_setting('app.tenant_id', TRUE));
