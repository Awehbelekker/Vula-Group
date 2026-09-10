-- 156_stock_sheets.sql — read a distributor stock sheet as ROWS, answer it deterministically.
--
-- 2026-09-07, gerflor: a rep uploaded "DT SOH and Planning 07.09.26.pdf" and asked "how's
-- Creation stock". Vula said it couldn't find anything and four rounds of retrieval tuning
-- never fixed it — because "Creation" is genuinely absent from that sheet. Similarity search
-- can rank what a document contains; it can never report what a document OMITS. Absence is a
-- fact about a SET, so the sheet is stored as a set of rows and queried directly
-- (vula/commerce/stock_sheet.py), not embedded as prose.
--
-- One row per (tenant, source document). A re-upload of the same sheet replaces its rows;
-- answer_stock_query() always reads the most recently updated sheet for the tenant.

create table if not exists vula_stock_sheets (
    tenant_id       text not null,
    doc_id          text not null,          -- ingestion pipeline's content-hashed doc id
    filename        text,
    as_at           text,                   -- the sheet's own "SOH m² – 07.09.26" date, verbatim
    rows            jsonb not null default '[]'::jsonb,   -- [{product,colour,thickness,soh,inbound,note}]
    product_ranges  jsonb not null default '[]'::jsonb,   -- distinct ranges — the answer when the asked-for one is absent
    created_at      timestamptz not null default now(),
    updated_at      timestamptz not null default now(),
    primary key (tenant_id, doc_id)
);

create index if not exists vula_stock_sheets_recent_idx
    on vula_stock_sheets (tenant_id, updated_at desc);

alter table vula_stock_sheets enable row level security;

drop policy if exists vula_stock_sheets_service on vula_stock_sheets;
create policy vula_stock_sheets_service on vula_stock_sheets
    for all to service_role using (true) with check (true);
