-- ============================================================
-- Vula Group — Migration 174: commerce_suppliers.updated_at (schema drift)
--
-- 2026-09-23: every UPDATE on commerce_suppliers fails in production with
--   record "new" has no field "updated_at"  (42703)
-- Migration 009 declared updated_at inside CREATE TABLE IF NOT EXISTS, but the table already
-- existed in production, so the column was never added, while 009's trigger
-- trg_suppliers_updated_at (set_updated_at()) was. Found when saving a supplier alias from
-- WhatsApp ("Jack Hammer is an alias for Gardens Handiman Centre") failed on digg-demo.
-- Idempotent. RLS is already enabled on this table (migration 009).
-- ============================================================

ALTER TABLE commerce_suppliers
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
