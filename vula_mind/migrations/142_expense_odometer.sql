-- 142_expense_odometer.sql — odometer reading at a petrol fill-up, for a real KM logbook.
--
-- Ian's original real claim-sheet template (the .xlsx he shared) has a KM column specifically
-- in its PETROL section — needed for South African travel-allowance/logbook purposes, not just
-- "how much did fuel cost." Only ever asked/shown for claims classified purpose_category='petrol'
-- (migration 140) — other categories have no odometer concept.

alter table commerce_expenses
    add column if not exists odometer_km integer;
