-- 287_books_audit.sql — the books audit (Phase 0 of Xero-quality books) and official-statement checks.
--
-- 17 Nov 2026 (Ian, DIGG): before an accountant gets access, Vula audits the bank data under the
-- books (vula/commerce/books_audit.py) and compares each official bank statement, line by line,
-- with what Vula holds (vula/commerce/statement_compare.py). A comparison only proposes; nothing
-- changes until an owner or manager confirms, and what they confirmed is kept here.
-- Idempotent.

create table if not exists vula_books_audits (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      text not null,
    report         jsonb not null,
    summary        jsonb not null default '{}'::jsonb,
    created_by     text,
    created_at     timestamptz not null default now(),
    signed_off_by  text,
    signed_off_at  timestamptz
);
create index if not exists vula_books_audits_tenant on vula_books_audits (tenant_id, created_at desc);
alter table vula_books_audits enable row level security;

create table if not exists vula_statement_checks (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       text not null,
    filename        text not null,
    account_number  text,
    bank            text,
    period_from     date,
    period_to       date,
    opening_cents   bigint,
    closing_cents   bigint,
    actions         jsonb not null default '[]'::jsonb,
    summary         jsonb not null default '{}'::jsonb,
    status          text not null default 'proposed' check (status in ('proposed', 'applied', 'discarded')),
    created_by      text,
    created_at      timestamptz not null default now(),
    applied_by      text,
    applied_at      timestamptz,
    applied         jsonb
);
create index if not exists vula_statement_checks_tenant on vula_statement_checks (tenant_id, created_at desc);
alter table vula_statement_checks enable row level security;

-- why a bank line is set aside ("not on the official statement", "misread copy", …)
alter table commerce_bank_transactions add column if not exists excluded_reason text;
