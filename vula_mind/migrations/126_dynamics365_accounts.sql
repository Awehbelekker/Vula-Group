-- 126_dynamics365_accounts.sql — per-tenant Dynamics 365 (Dataverse) connection.
--
-- Reuses the SAME multi-tenant Azure app already used for Microsoft 365 (OneDrive/Outlook,
-- migration 019) — settings.microsoft_client_id/secret — just with the Dataverse API
-- permission added to that app registration (an Azure-portal action, not a schema change).
-- Unlike Graph (one universal resource), Dataverse tokens are scoped to a specific org's URL
-- (https://<org>.crm.dynamics.com/.default), so org_url is captured at connect time and
-- stored alongside the tokens.

create table if not exists vula_dynamics365_accounts (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     text not null unique,
    org_url       text not null,
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
create index if not exists idx_vula_dynamics365_accounts_tenant on vula_dynamics365_accounts (tenant_id);

alter table vula_dynamics365_accounts enable row level security;
drop policy if exists "tenant_isolation" on vula_dynamics365_accounts;
create policy "tenant_isolation" on vula_dynamics365_accounts
    using (tenant_id = current_setting('app.tenant_id', true));
