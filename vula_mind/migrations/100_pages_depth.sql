-- 100_pages_depth.sql (renumbered from 081 — collided with 081_dedupe_filed_documents.sql) —
-- Pages editor depth (P4): reordering + revision history.
-- The Puck builder had draft/publish/SEO/templates/18 blocks but no way to reorder pages in the
-- list, and publishing overwrote a page with no way back to yesterday's version.

alter table vula_pages add column if not exists sort_order integer not null default 0;

-- One row per PUBLISH (not every draft autosave, to keep history meaningful) — snapshots the
-- page's state right before the new version overwrites it, so "Restore" always has something
-- to go back to.
create table if not exists vula_page_versions (
    id uuid primary key default gen_random_uuid(),
    page_id uuid not null references vula_pages(id) on delete cascade,
    title text,
    puck_data jsonb,
    seo jsonb,
    status text,
    created_at timestamptz not null default now()
);

create index if not exists idx_page_versions_page on vula_page_versions(page_id, created_at desc);
