-- 027_project_finances.sql — money in/out per project, from filed financial docs.
-- Each invoice/payment the AI extracts an amount from posts a ledger row; budgets
-- enable budget-vs-actual (QS).
create table if not exists vula_project_finances (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    project      text,
    doc_id       text,
    filename     text,
    direction    text not null default 'unknown',   -- in | out | unknown
    amount       numeric not null default 0,
    counterparty text,
    bank_account text,                               -- beneficiary account no. (strong supplier id)
    reference    text,
    category     text,
    kind         text default 'other',              -- invoice | payment | other
    description  text,                               -- what it's for (line items / summary)
    matched_id   uuid,                               -- reconciled counterpart (invoice<->payment)
    reconciled   boolean default false,
    occurred_at  date,
    source       text default 'email',
    created_at   timestamptz not null default now(),
    unique (tenant_id, doc_id)
);
create index if not exists idx_vula_finances_tenant on vula_project_finances (tenant_id, project);

create table if not exists vula_project_budgets (
    tenant_id  text not null,
    project    text not null,
    budget     numeric not null default 0,
    updated_at timestamptz default now(),
    primary key (tenant_id, project)
);
