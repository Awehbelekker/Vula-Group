-- 133_fix_part_paid_status_constraint.sql — fixes a real bug caught by live verification of
-- migration 130's partial-payments feature: 053_invoice_discounts_deposits.sql's comment
-- claimed "'part_paid' is a new status value (status is free text; no enum change needed)",
-- but 008_invoice_doc_types.sql actually DOES constrain commerce_invoices.status via a CHECK
-- constraint, and its list never included 'part_paid'. Every attempted partial payment has been
-- failing at the DB layer since the day migration 053 shipped (2026-07 or earlier) — the
-- application code (record_invoice_payment) was correct, the column existed, but the write was
-- silently impossible. Confirmed live (2026-08-15): a real test payment insert against
-- off-the-hook raised commerce_invoices_status_check before this fix, and succeeded after.

ALTER TABLE commerce_invoices DROP CONSTRAINT IF EXISTS commerce_invoices_status_check;
ALTER TABLE commerce_invoices
    ADD CONSTRAINT commerce_invoices_status_check
    CHECK (status IN (
        'draft','sent','paid','overdue','cancelled','part_paid',  -- invoice lifecycle
        'accepted','declined','expired'                           -- quote lifecycle
    ));
