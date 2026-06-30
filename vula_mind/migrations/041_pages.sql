-- ============================================================
-- Vula Group — Migration 041: CMS pages (Puck self-serve page builder, P3)
-- Per-tenant pages stored as Puck JSON. The storefront renders published pages;
-- the store /admin edits them via Puck. Run in Supabase SQL editor.
-- ============================================================

create table if not exists vula_pages (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    slug        text not null,
    title       text,
    puck_data   jsonb not null default '{}'::jsonb,   -- Puck document (block tree)
    seo         jsonb not null default '{}'::jsonb,    -- {title, description, image}
    status      text not null default 'draft',         -- draft | published
    updated_at  timestamptz not null default now(),
    created_at  timestamptz not null default now(),
    unique (tenant_id, slug)
);

create index if not exists idx_vula_pages_tenant on vula_pages (tenant_id, status);

alter table vula_pages enable row level security;
drop policy if exists "tenant_isolation" on vula_pages;
create policy "tenant_isolation" on vula_pages
    using (tenant_id = current_setting('app.tenant_id', true));
