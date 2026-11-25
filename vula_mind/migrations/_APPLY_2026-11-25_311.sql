-- Paste into the Supabase SQL editor (clear the editor first). Safe to run twice.
begin;
create table if not exists commerce_spec_imports (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    file_name    text,
    status       text not null default 'reading',     -- reading | ready | failed
    pages        integer,
    pages_read   integer not null default 0,
    result       jsonb not null default '{}'::jsonb,  -- {items: [...], skipped_pages, notes}
    error        text,
    applied      jsonb not null default '[]'::jsonb,  -- item keys already applied
    created_by   text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);
create index if not exists idx_spec_imports_tenant on commerce_spec_imports (tenant_id, created_at desc);

alter table commerce_spec_imports enable row level security;
drop policy if exists "tenant_isolation" on commerce_spec_imports;
create policy "tenant_isolation" on commerce_spec_imports
    using (tenant_id = current_setting('app.tenant_id', true));

comment on table commerce_spec_imports is 'A maker''s catalogue PDF read for spec sheets: what was found, matched to the business''s products, waiting for the person to keep what is right.';
insert into vula_schema_migrations (filename, number, sha256, applied_by, baseline)
values ('311_spec_imports.sql', 311, '04d27a5eeddecd17d8ffd548c64c4b9005d15ed8af34a5115410bcd862943146', 'sql-editor', false) on conflict do nothing;
commit;
