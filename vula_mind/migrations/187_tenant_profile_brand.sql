-- 187_tenant_profile_brand.sql — who a business is (aliases, description) and the rest of its
-- brand (secondary colour, corners, density, tagline, app icon).
--
-- 2026-09-29 (Ian: "same feel on every page… modern and slick, customised to each tenant").
-- tenants.tenant_profile() reads aliases/description: the names a business goes by (DIGG's
-- "Aweh Be Lekker t/a DIGG Collection") so its own name is never a filing clue, and a line it can
-- be introduced with. The brand kit (commerce_invoice_settings — the ONE brand source, read by
-- /v1/commerce/{t}/brand, the dashboard, PDFs and emails) gains the fields the dashboard theme
-- needs. Idempotent; no new tables.

alter table vula_tenant_config
    add column if not exists aliases text[],
    add column if not exists description text;

alter table commerce_invoice_settings
    add column if not exists secondary_color text,
    add column if not exists corner_style text,     -- 'rounded' | 'soft' | 'sharp'
    add column if not exists density text,          -- 'comfortable' | 'compact'
    add column if not exists tagline text,
    add column if not exists icon_url text;         -- square app icon (PWA, favicon)
