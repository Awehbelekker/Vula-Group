-- ============================================================
-- Vula Group — Migration 008: Invoice document types (quotes/proformas)
-- Extends commerce_invoices (006) so one table serves invoices,
-- quotes, and proformas, with quote→invoice conversion linkage.
-- Run in: Supabase Dashboard > SQL Editor > New Query > Run
-- Depends on: 006_invoices_broadcasts_expenses.sql
-- ============================================================

-- ── Document type (invoice | quote | proforma) ────────────────────────────────
ALTER TABLE commerce_invoices
    ADD COLUMN IF NOT EXISTS doc_type TEXT NOT NULL DEFAULT 'invoice';

ALTER TABLE commerce_invoices DROP CONSTRAINT IF EXISTS commerce_invoices_doc_type_chk;
ALTER TABLE commerce_invoices
    ADD CONSTRAINT commerce_invoices_doc_type_chk
    CHECK (doc_type IN ('invoice','quote','proforma'));

-- ── Quote-specific + conversion linkage columns ───────────────────────────────
ALTER TABLE commerce_invoices
    ADD COLUMN IF NOT EXISTS valid_until DATE;                 -- quote validity
ALTER TABLE commerce_invoices
    ADD COLUMN IF NOT EXISTS source_quote_id UUID REFERENCES commerce_invoices(id);
ALTER TABLE commerce_invoices
    ADD COLUMN IF NOT EXISTS converted_invoice_id UUID REFERENCES commerce_invoices(id);

-- ── Expand the status set to cover the quote lifecycle ─────────────────────────
-- 006 defined an inline CHECK named commerce_invoices_status_check.
ALTER TABLE commerce_invoices DROP CONSTRAINT IF EXISTS commerce_invoices_status_check;
ALTER TABLE commerce_invoices
    ADD CONSTRAINT commerce_invoices_status_check
    CHECK (status IN (
        'draft','sent','paid','overdue','cancelled',   -- invoice lifecycle
        'accepted','declined','expired'                -- quote lifecycle
    ));

-- ── Helpful indexes for the new dimensions ────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_invoices_doc_type
    ON commerce_invoices(tenant_id, doc_type);
CREATE INDEX IF NOT EXISTS idx_invoices_source_quote
    ON commerce_invoices(source_quote_id);
