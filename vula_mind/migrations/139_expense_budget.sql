-- 139_expense_budget.sql — per-rep expense budget (sales rep dashboard).
-- Tracking itself needs no new infrastructure: spend-to-date is a live SUM(amount_cents) against
-- commerce_expenses, which already accumulates in real time as receipts are scanned. This column
-- is just the standing "what's the budget" config, folded into configure_call_sheet's existing
-- per-rep config row (vula_team_members) alongside the call-sheet columns from migration 138.
alter table vula_team_members
    add column if not exists expense_budget_cents bigint,
    add column if not exists expense_budget_warned_month text,
    add column if not exists expense_budget_warned_pct smallint;
