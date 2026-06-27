-- 019_microsoft_accounts.sql — per-tenant Microsoft connection (OneDrive + Outlook).
--
-- One Microsoft (Azure AD) app for all Vula tenants; each tenant connects their own
-- Microsoft 365 / personal account (one-click, like Google). Refresh token stored for
-- offline access; short-lived access token refreshed on demand via Microsoft Graph.

create table if not exists vula_microsoft_accounts (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null unique,
    email         text,
    access_token  text,
    refresh_token text,
    token_expiry  timestamptz,
    scopes        text,
    status        text not null default 'connected',
    connected_by  text,
    connected_at  timestamptz,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
create index if not exists idx_vula_microsoft_accounts_tenant on vula_microsoft_accounts (tenant_id);
