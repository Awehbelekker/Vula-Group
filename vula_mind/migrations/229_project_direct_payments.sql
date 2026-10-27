-- 229_project_direct_payments.sql — what the client paid suppliers and trades directly.
--
-- 27 Oct 2026 (DIGG, Belladonna): Rudi paid Flush, Edward (joinery), Elyas (electrical), Edison
-- (plumbing), Jack Hammer, Cash Tile and Bathroom Bizarre himself — R283,816.08, with R64,270
-- still to pay Edward and Elyas. The final account was built around those figures, but Vula had
-- nowhere to record them: a supplier invoice's direct_supplier flag could only be set by hand in
-- the database, and a payment with no document at all couldn't be recorded. The project tracker
-- (vula/commerce/project_tracker.py) showed R0 paid directly.
--
-- One row per payment the client made (status 'paid') or has still to make ('to_pay') to a
-- supplier or trade on a project. In the project's spend against its budget; never in the
-- business's cost, fee or profit. 'removed' takes a wrong entry out without deleting it.
-- Money in integer cents. RLS on, no policies (service key only). Idempotent.

create table if not exists vula_project_direct_payments (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    project      text not null,
    supplier     text not null,
    description  text,
    amount_cents bigint not null check (amount_cents > 0),
    paid_on      date,
    status       text not null default 'paid' check (status in ('paid', 'to_pay', 'removed')),
    created_by   text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);
create index if not exists idx_project_direct_payments on vula_project_direct_payments (tenant_id, project);
alter table vula_project_direct_payments enable row level security;
