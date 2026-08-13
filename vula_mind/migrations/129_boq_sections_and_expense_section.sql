-- ============================================================
-- Vula Group — Migration 129: BoQ trade-section budgets + expense section tagging
--
-- Two gaps found in a 2026-08-12 deep-dive of DIGG's real money data:
-- 1) vula_project_boq (migration 056) only ever stored ONE lump total per project — a BoQ's
--    real trade-section breakdown (Demolition, Structure, etc.), which the takeoff generator
--    already produces transiently and which invoices already reference via their own
--    per-line `section` field (core/skills/commerce_admin.py's create_invoice), had nowhere
--    to persist. Adds `sections` as a JSONB array of {section, budget_cents}.
-- 2) commerce_expenses had no equivalent `section` field at all, so actual site spend could
--    never be compared against a BoQ trade section's budget — only against the whole
--    project's lump total. Mirrors the `project` column exactly (nullable text, no FK — same
--    open-ended "known projects" pattern already used for `project`).
--
-- Run in Supabase SQL editor.
-- ============================================================

alter table vula_project_boq add column if not exists sections jsonb not null default '[]'::jsonb;

alter table commerce_expenses add column if not exists section text;
create index if not exists idx_commerce_expenses_section on commerce_expenses (tenant_id, section)
    where section is not null;
