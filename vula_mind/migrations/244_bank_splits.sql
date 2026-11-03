-- 244_bank_splits.sql — one bank payment split across projects.
--
-- 3 Nov 2026: a R19,000 payment to a subcontractor paid workers on two jobs
-- (R15,050 for one, the rest for the other), but a bank line could only carry one project, so one
-- project's costs were too high and the other's too low. A split gives parts of the line to other
-- projects; whatever the parts don't cover stays on the line's own project. The bank line itself
-- (amount, date, balance) is never changed, so the statement still reconciles.
--
--   txn_id        the commerce_bank_transactions row being split
--   project       the project this part belongs to
--   amount_cents  this part, > 0; the active parts of a line never add up to more than the line
--   status        active | replaced (a new split of the same line replaces the old one; never deleted)
--
-- Read and written by the backend with the service key only. RLS on, no policies. Idempotent.
create table if not exists commerce_bank_splits (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null,
    txn_id        uuid not null references commerce_bank_transactions(id) on delete cascade,
    project       text not null,
    amount_cents  bigint not null check (amount_cents > 0),
    account_code  text,
    note          text,
    status        text not null default 'active' check (status in ('active', 'replaced')),
    created_by    text,
    created_at    timestamptz not null default now()
);
create index if not exists idx_bank_splits_txn on commerce_bank_splits (tenant_id, txn_id, status);
create index if not exists idx_bank_splits_project on commerce_bank_splits (tenant_id, project, status);
alter table commerce_bank_splits enable row level security;
