-- 184_price_observations.sql — the tenant's price book: every priced line on every document.
--
-- 2026-09-28 (Ian: "the QS rates are not adapting"). digg-demo had ~1,000 priced line items on
-- filed invoices, quotes and BOQs, and 2 rows in vula_qs_rates — nothing ever turned a
-- document's prices into a rate. Each filed document's line items are now recorded here (one
-- row per line, replaced when the document is re-read), plus casual-labour day rates, and
-- vula/commerce/price_book.py rolls them up into learned rates (latest / median / range per
-- item) that the QS screens, the calculations skill and Takeoff pricing read after the
-- tenant's own manual rates. A manual rate is never changed by this.
--
-- Money is integer cents (unit_price_cents). Idempotent.

create table if not exists vula_price_observations (
    id               uuid primary key default gen_random_uuid(),
    tenant_id        text not null,
    doc_id           text,            -- vula_filed_documents.id, or 'worker:<id>' for labour rates
    source_kind      text not null,   -- invoice | quote | boq | expense | labour_payment
    supplier         text,
    project          text,
    description      text not null,
    norm_key         text not null,
    unit             text,
    kind             text not null default 'material',  -- material | labour | plant | delivery | other
    section          text,
    quantity         numeric,
    unit_price_cents bigint not null,
    observed_on      date,
    created_at       timestamptz not null default now()
);
create index if not exists idx_price_obs_tenant_key on vula_price_observations (tenant_id, norm_key);
create index if not exists idx_price_obs_doc on vula_price_observations (tenant_id, doc_id);
alter table vula_price_observations enable row level security;
