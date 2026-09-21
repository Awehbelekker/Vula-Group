-- 171_qdrant_backup_status.sql — per-tenant Qdrant collection backup status + private storage
-- bucket for the snapshot files themselves.
--
-- docs/dr.md flagged Qdrant (self-hosted per-tenant RAG vector store) as having zero backup
-- mechanism — the single highest-severity finding in the go-live readiness DR review. This adds
-- the state a periodic snapshot job (vula/integrations/qdrant_backup.py) needs: one row per
-- tenant recording the outcome of its last backup, and a private bucket to hold the snapshot
-- files. UPSERT target (no pre-existing per-tenant row the way ClickUp/OneDrive accounts have),
-- unlike sync_status.py's record_sync_result which is UPDATE-only.

create table if not exists vula_qdrant_backup_status (
    tenant_id text primary key,
    last_backup_at timestamptz,
    last_backup_status text,   -- 'ok' | 'error'
    last_backup_error text,
    last_snapshot_path text,
    updated_at timestamptz not null default now()
);

alter table vula_qdrant_backup_status enable row level security;

create policy "tenant_isolation" on vula_qdrant_backup_status
    using (tenant_id = current_setting('app.tenant_id', true));

-- Private bucket: unlike 'signatures'/'product-images' this is never touched from the browser
-- or dashboard, only the backend's service-role client (which bypasses RLS), and it holds raw
-- tenant knowledge-base content — so no storage.objects policies are needed, and public=false.
insert into storage.buckets (id, name, public)
values ('qdrant-backups', 'qdrant-backups', false)
on conflict (id) do nothing;
