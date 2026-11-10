-- 256_project_fee_per_bill.sql — a third way a project's price is agreed: as per the bill.
--
-- 10 Nov 2026 (Ian, DIGG): "Belladonna was agreed as per the bill." The fee is inside the bill's own
-- rates (P&G at R12,000 a week is a line on the bill), so there is no percentage on cost and no fee to
-- add: the client pays the bill, plus agreed variations. Such a project's account is its invoices — or
-- its final account (migration 254) once there is one.
--
--   pricing   cost_plus | fixed_fee | per_bill   (per_bill carries no fee_pct and no fee_cents)
-- Idempotent.

alter table vula_project_terms drop constraint if exists vula_project_terms_pricing_check;
alter table vula_project_terms add constraint vula_project_terms_pricing_check
    check (pricing in ('cost_plus', 'fixed_fee', 'per_bill'));
alter table vula_project_terms drop constraint if exists vula_project_terms_fee_check;
alter table vula_project_terms add constraint vula_project_terms_fee_check
    check ((pricing = 'cost_plus' and fee_pct is not null and fee_pct >= 0 and fee_pct <= 100)
        or (pricing = 'fixed_fee' and fee_cents is not null and fee_cents >= 0)
        or (pricing = 'per_bill' and fee_pct is null and fee_cents is null));
alter table vula_project_terms enable row level security;
