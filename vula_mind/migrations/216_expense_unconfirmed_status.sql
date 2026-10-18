-- 216_expense_unconfirmed_status.sql — allow commerce_expenses.status = 'unconfirmed'.
--
-- 2026-10-18: #115 (expense confirm mode) records a receipt Vula wasn't told to book as
-- status 'unconfirmed' (vula/commerce/expenses.py record_claim(confirmed=False)), kept out of
-- the ledger until the owner taps Book (confirm_claim → 'submitted'). The 060 CHECK never
-- listed 'unconfirmed', so every such insert was rejected by the database and the receipt was
-- lost. This re-adds the CHECK with 'unconfirmed' included; nothing else changes.
--
-- RLS on commerce_expenses is already on (006). Idempotent: drop-if-exists, then add.

do $$
begin
    if exists (select 1 from pg_constraint where conname = 'commerce_expenses_status_check') then
        alter table commerce_expenses drop constraint commerce_expenses_status_check;
    end if;
end $$;

alter table commerce_expenses
    add constraint commerce_expenses_status_check
    check (status in ('pending','unconfirmed','submitted','approved','paid','reimbursed','rejected','cancelled'));
