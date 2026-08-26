-- 141_expense_purpose_detail.sql — free-text detail for an expense's purpose category.
--
-- Companion to migration 140's commerce_expenses.purpose_category: when a claim can't be
-- confidently auto-classified into petrol/clients/accommodation and a rep answers in their own
-- words (e.g. "coffee with a client"), the resolved bucket is still one of the known categories
-- ('other' if nothing matches) but the rep's own wording is preserved here rather than dropped.

alter table commerce_expenses
    add column if not exists purpose_detail text;
