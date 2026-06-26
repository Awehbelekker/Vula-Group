-- 011_document_extractions.sql
-- Structured analysis of uploaded documents (the "allocate info into the backend"
-- store). Each row is one analysed upload: its category, a short summary, and the
-- structured fields the analyzer pulled out (totals, line items, dates, parties…).

create table if not exists vula_document_extractions (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    filename     text not null,
    category     text,                      -- Invoice | BOQ | Fee Proposal | …
    summary      text,                      -- 1–2 sentence plain-English summary
    fields       jsonb default '{}'::jsonb, -- structured key/value extraction
    source       text default 'whatsapp',   -- where the upload came from
    doc_id       text,                      -- links to the KB doc id
    created_at   timestamptz not null default now()
);

create index if not exists idx_doc_extractions_tenant
    on vula_document_extractions (tenant_id, created_at desc);

create index if not exists idx_doc_extractions_category
    on vula_document_extractions (tenant_id, category);
