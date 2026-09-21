-- 169_integration_sync_status.sql — persist background-sync outcome per tenant.
--
-- process_all_clickup_sync/process_all_onedrive_sync (vula/clickup/service.py,
-- vula/microsoft/service.py) already run per connected tenant, but only `logger.warning` on
-- failure — nothing persisted. The dashboard's connect-status UI (VulaClickUpConnect.jsx,
-- VulaMicrosoftConnect.jsx) shows "Connected" purely from OAuth token presence, so a tenant
-- whose sync has been silently failing for days still sees a green badge. This adds the
-- columns the sync loops now write to and the status endpoints now read from.

alter table vula_clickup_accounts
    add column if not exists last_synced_at timestamptz,
    add column if not exists last_sync_status text,
    add column if not exists last_sync_error text;

alter table vula_microsoft_accounts
    add column if not exists last_synced_at timestamptz,
    add column if not exists last_sync_status text,
    add column if not exists last_sync_error text;
