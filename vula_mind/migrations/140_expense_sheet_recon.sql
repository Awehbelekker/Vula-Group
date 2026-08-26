-- 140_expense_sheet_recon.sql — monthly expense-sheet scheduling + Recon-style categorisation.
--
-- Category vocabulary matches Ian's REAL, already-in-use claim sheet (a real .xlsx he shared,
-- 2026-08-26): a "Recon" summary sheet grouping claims into PETROL / CLIENTS (refreshments) /
-- ACCOMMODATION, plus per-category sheets holding the actual slip images — not a generic
-- SARS-style list invented from scratch. "other" is the catch-all for anything that doesn't
-- fit those three (e.g. parking, a car rental charge).

alter table vula_team_members
    add column if not exists expense_sheet_recipient_email text,
    add column if not exists expense_sheet_day_of_month     smallint not null default 1,  -- 1-28
    add column if not exists expense_sheet_last_sent_at      timestamptz;

alter table commerce_expenses
    add column if not exists purpose_category text;  -- 'petrol' | 'clients' | 'accommodation' | 'other', null = not yet classified
