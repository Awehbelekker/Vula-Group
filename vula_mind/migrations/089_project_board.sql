-- ============================================================
-- Vula Group — Migration 089: Project board (Milanote-style cards)
-- Freeform-positioned note/image/link/checklist cards on a per-project board,
-- nested inside the existing Project Workspace (alongside brief/tasks/threads).
-- "Board" is implicitly (tenant_id, project) — same key already used by
-- vula_project_briefs/vula_project_tasks — no separate boards table for v1.
-- Card images reuse the existing "product-images" Storage bucket (its RLS
-- policy checks only bucket_id, not folder — see 082_product_images_storage_policy.sql
-- — so no new storage policy is needed here) under {tenant_id}/board/{project}/...
-- Run in Supabase SQL editor.
-- ============================================================

create table if not exists vula_project_cards (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    project      text not null,
    card_type    text not null check (card_type in ('note', 'image', 'link', 'checklist')),
    x            numeric not null default 40,
    y            numeric not null default 40,
    width        numeric not null default 220,
    height       numeric not null default 160,
    z_index      integer not null default 1,
    color        text,                          -- sticky tint, e.g. '#FFF3C4'; null = default
    content      jsonb not null default '{}'::jsonb,
    created_by   text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);
create index if not exists idx_project_cards_scope on vula_project_cards (tenant_id, project);

alter table vula_project_cards enable row level security;
drop policy if exists "tenant_isolation" on vula_project_cards;
create policy "tenant_isolation" on vula_project_cards
    using (tenant_id = current_setting('app.tenant_id', true));

drop trigger if exists trg_project_cards_updated_at on vula_project_cards;
create trigger trg_project_cards_updated_at
    before update on vula_project_cards
    for each row execute function set_updated_at();
