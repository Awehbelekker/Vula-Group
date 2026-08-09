-- 125_progress_claims.sql — structured progress claims / interim payment certificates.
-- JBCC-style: each claim states the CUMULATIVE value of work completed to date; retention
-- and "this payment" are always computed (never trusted from the caller) from that figure
-- and the previous claim's certified-to-date total. Distinct from vula/api/draft.py's
-- payment_certificate template, which only drafts prose — this is the persisted, calculated
-- record a real certificate/invoice is generated FROM.
create table if not exists vula_project_claims (
    id                        uuid primary key default gen_random_uuid(),
    tenant_id                 text not null,
    project                   text not null,
    claim_number              integer not null,
    claim_date                date not null default current_date,
    cumulative_value_cents    bigint not null,              -- value of ALL work done to date
    retention_pct             numeric not null default 5.0,
    retention_cents           bigint not null,
    certified_to_date_cents   bigint not null,               -- cumulative_value - retention
    previous_certified_cents  bigint not null default 0,
    this_payment_cents        bigint not null,               -- certified_to_date - previous_certified
    status                    text not null default 'draft', -- draft | certified | invoiced
    notes                     text,
    linked_invoice_id         uuid references commerce_invoices(id),
    created_at                timestamptz not null default now(),
    certified_at              timestamptz,
    unique (tenant_id, project, claim_number)
);
create index if not exists idx_project_claims_tenant on vula_project_claims (tenant_id, project);

alter table vula_project_claims enable row level security;
drop policy if exists "tenant_isolation" on vula_project_claims;
create policy "tenant_isolation" on vula_project_claims
    using (tenant_id = current_setting('app.tenant_id', true));
