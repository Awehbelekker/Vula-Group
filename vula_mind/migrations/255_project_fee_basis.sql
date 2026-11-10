-- 255_project_fee_basis.sql — each project's fee as agreed: a percentage on cost, or a fixed amount.
--
-- 10 Nov 2026 (Ian, DIGG): "each project will or can work differently — 10% fee or an agreed amount
-- per project … with new tenants and going forward we need to be accurate." No tenant had ever set a
-- fee (vula_project_terms was empty), so every open-book link showed a 10% the code assumed — and
-- Belladonna's client was shown the business owing him R57,917.27 when he owed it R32,839.40.
--
--   pricing     cost_plus  the client pays cost × (1 + fee_pct / 100)
--               fixed_fee  the client pays cost + fee_cents (an agreed amount, e.g. P&G R48,000)
--   fee_cents   the agreed fixed fee, in integer cents (fixed_fee only)
--   agreed_by   who set it, for the record
--
-- A row means the fee was agreed; no row means it wasn't, and the client's link then shows no
-- balance (vula/commerce/project_tracker.py). project '*' is the business's standard fee.
-- A project's final account (migration 254), when there is one, overrides both. Idempotent.

alter table vula_project_terms
    add column if not exists fee_cents bigint,
    add column if not exists agreed_by text;
alter table vula_project_terms alter column fee_pct drop not null;

do $$ begin
    alter table vula_project_terms add constraint vula_project_terms_pricing_check
        check (pricing in ('cost_plus', 'fixed_fee'));
exception when duplicate_object then null; end $$;
do $$ begin
    alter table vula_project_terms add constraint vula_project_terms_fee_check
        check ((pricing = 'cost_plus' and fee_pct is not null and fee_pct >= 0 and fee_pct <= 100)
            or (pricing = 'fixed_fee' and fee_cents is not null and fee_cents >= 0));
exception when duplicate_object then null; end $$;

alter table vula_project_terms enable row level security;
