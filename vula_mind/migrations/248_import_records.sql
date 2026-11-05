-- 248_import_records.sql — everything read from the old accounting app, kept as it was.
--
-- 5 Nov 2026 (Move from Xero, step 2): the move is a one-off pull, then Vula disconnects. Every
-- record read — invoices, bills, credit notes, payments, bank transactions, manual journals,
-- contacts, accounts, items — is kept here exactly as the old app gave it (`data`), so nothing is
-- lost when the connection is gone and Vula can answer questions about the old years. The live
-- books get only what runs the business (opening balances, documents, contacts, accounts, items);
-- bank transactions and journals stay here, because their effect is already in the opening
-- balances and posting them again would count them twice.
--
-- Read and written by the backend with the service key only. RLS on, no policies. Idempotent.
create table if not exists vula_import_records (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    import_id    uuid,
    source       text not null,
    kind         text not null,          -- invoice | bill | credit_note | payment | bank_transaction | ...
    source_id    text not null,
    record_date  date,
    reference    text,                   -- the document number or bank reference
    contact      text,
    amount_cents bigint,
    data         jsonb not null default '{}',
    created_at   timestamptz not null default now()
);
create unique index if not exists idx_import_records_key
    on vula_import_records (tenant_id, source, kind, source_id);
create index if not exists idx_import_records_find
    on vula_import_records (tenant_id, kind, record_date desc);
alter table vula_import_records enable row level security;
