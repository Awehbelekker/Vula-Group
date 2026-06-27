-- 018_google_accounts.sql — per-tenant Google connection (Drive + Gmail).
--
-- One Google OAuth app for all Vula tenants; each tenant connects their own Google
-- account (one-click, like ClickUp). We store the refresh token for offline access
-- and refresh the short-lived access token on demand.

create table if not exists vula_google_accounts (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null unique,
    email         text,                          -- the connected Google account
    access_token  text,
    refresh_token text,                           -- long-lived (offline access)
    token_expiry  timestamptz,                    -- when access_token expires
    scopes        text,
    status        text not null default 'connected', -- connected | error | disconnected
    connected_by  text,
    connected_at  timestamptz,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
create index if not exists idx_vula_google_accounts_tenant on vula_google_accounts (tenant_id);
