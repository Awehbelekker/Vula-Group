-- 238_sheet_specs.sql — spreadsheets the owner describes in words: drafts and saved layouts.
--
-- 31 Oct 2026 (Ian: "plan the sheet builder"). The owner asks for a sheet in their own words; Vula
-- turns it into a spec (data source, filters, columns, grouping, totals — vula/commerce/
-- sheet_builder.py), shows a preview, and builds the file from the books once the owner agrees.
-- One row per draft (status draft, then built) and per saved layout (status template, with a
-- name: "the Tile Co monthly sheet"). The spec holds no figures from the books, only the layout;
-- a draft also keeps the preview's totals so the build can prove it matches what was shown.
--
-- Read and written only by the API with the service key. RLS on, no policies. Idempotent.

create table if not exists vula_sheet_specs (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      text not null,
    name           text,
    status         text not null default 'draft'
                   check (status in ('draft', 'built', 'template', 'archived')),
    spec           jsonb not null,
    request        text,
    template_id    uuid,
    created_by     text,
    uses           integer not null default 0,
    last_built_at  timestamptz,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);
create index if not exists idx_sheet_specs_recent on vula_sheet_specs (tenant_id, created_by, created_at desc);
create unique index if not exists uq_sheet_templates on vula_sheet_specs (tenant_id, lower(name))
    where status = 'template';
alter table vula_sheet_specs enable row level security;
