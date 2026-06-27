-- 026_filing_rules.sql — Vula learns from corrections.
-- When a human answers "which project?", the signals in that document (reference,
-- payee, payer, supplier…) are remembered → similar docs auto-file next time, no ask.
create table if not exists vula_filing_rules (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    signal      text not null,          -- normalised value, e.g. 'diggwage', 'nfuma'
    signal_type text not null,          -- reference | payee | payer | sender | keyword
    project     text not null,
    hits        integer not null default 1,
    last_used   timestamptz default now(),
    created_at  timestamptz default now(),
    unique (tenant_id, signal, project)
);
create index if not exists idx_filing_rules_tenant on vula_filing_rules (tenant_id, signal);
