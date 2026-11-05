-- 247_xero_and_imports.sql — moving a business's books from Xero (later QuickBooks, Sage) into Vula.
--
-- 5 Nov 2026: owners want to bring their whole books over and leave their old accounting app. Step 1
-- connects Xero (read-only) and builds a preview at a cut-over date: the chart of accounts and how
-- it maps, customers and suppliers (matched, never duplicated), open invoices and bills, the opening
-- trial balance. Nothing is written to the books until the owner confirms (a later step); every
-- imported record is remembered once, so a second run never brings it in twice.
--
--   vula_xero_accounts  one Xero organisation per business: encrypted tokens (Fernet), who connected
--   vula_imports        one row per run: source, cut-over date, status, the preview, the check result
--   vula_import_map     source record → Vula record, per business (the "only once" key)
--
-- Read and written by the backend with the service key only. RLS on, no policies. Idempotent.
create table if not exists vula_xero_accounts (
    tenant_id        text primary key,
    xero_tenant_id   text,
    org_name         text,
    access_token     text,
    refresh_token    text,
    token_expiry     timestamptz,
    scopes           text,
    status           text not null default 'connected'
                     check (status in ('connected', 'needs_reconnect', 'disconnected')),
    connected_by     text,
    connected_at     timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    last_error       text
);
alter table vula_xero_accounts enable row level security;

create table if not exists vula_imports (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    text not null,
    source       text not null check (source in ('xero', 'quickbooks', 'sage', 'file')),
    cutover_date date not null,
    status       text not null default 'reading'
                 check (status in ('reading', 'previewed', 'importing', 'done', 'failed', 'undone')),
    preview      jsonb not null default '{}',
    result       jsonb not null default '{}',
    error        text,
    started_by   text,
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now()
);
create index if not exists idx_imports_tenant on vula_imports (tenant_id, created_at desc);
alter table vula_imports enable row level security;

create table if not exists vula_import_map (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   text not null,
    import_id   uuid,
    source      text not null,
    source_type text not null,                     -- account | contact | item | invoice | bill | ...
    source_id   text not null,
    vula_table  text not null,
    vula_id     text not null,
    created_at  timestamptz not null default now()
);
create unique index if not exists idx_import_map_key
    on vula_import_map (tenant_id, source, source_type, source_id);
alter table vula_import_map enable row level security;
