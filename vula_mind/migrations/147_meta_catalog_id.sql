-- 147_meta_catalog_id.sql — where a tenant's connected Meta Commerce Manager catalog ID lives.
--
-- Confirmed live, 2026-08-25 (off-the-hook): GET /{phone_number_id}/whatsapp_commerce_settings
-- returned no catalog — no tenant on this platform has one connected yet. This column is the
-- other half of scripts/sync_meta_catalog.py: empty until a human connects a Commerce Manager
-- catalog to the tenant's WhatsApp Business Account (a one-time Meta-side step, same category as
-- the WhatsApp number connection itself — cannot be done from here) and records the catalog ID
-- here. See vula/api/whatsapp.py's _handle_native_order for the receiving side (already live
-- regardless of whether any tenant has a catalog yet — it just won't receive "order" webhooks
-- until one does).

alter table vula_tenant_config
    add column if not exists meta_catalog_id text;
